"""System health checks (Feature Stage 2 — System Health page).

One endpoint, seven components, all read-only and cheap (EXISTS /
LIMIT probes; COUNTs only where an exact number is displayed).
Follows the Database design in
``~/workspace/your_files/agent-docs/database/system-health-design.md``:
no new tables, a 10-second server cache, Super Admin only, and
``HEALTH_CHECK`` rows in ``system_event_logs`` only when a
component's status changes (or, while it stays broken, at most
once an hour) so the 1,000-row System Logs cap is never flooded
by polling.

Statuses: OK < WARNING < FAIL. DISABLED (MinIO/Redis turned
off) never affects the overall status.
"""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import inspect as sa_inspect

from app.core.config import settings
from app.database.connection import engine
from app.models.system import LogLevel
from app.services.system_log_service import record_system_log

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 10
LOG_REPEAT_SECONDS = 3600

OK = "OK"
WARNING = "WARNING"
FAIL = "FAIL"
DISABLED = "DISABLED"

_SEVERITY = {OK: 0, WARNING: 1, FAIL: 2}

_CACHE: dict = {"at": 0.0, "payload": None}
_LAST_HEALTH_LOG: dict[str, tuple[str, float]] = {}

_MIGRATION_REVISION_RE = re.compile(r'^revision\s*=\s*"([^"]+)"', re.M)
_MIGRATION_DOWN_RE = re.compile(r'^down_revision\s*=\s*(?:"([^"]+)"|None|\(([^)]*)\))', re.M)


def _now_naive() -> datetime:
    """Stored datetimes are naive UTC; window math must match them."""
    return datetime.now(UTC).replace(tzinfo=None)


def _as_utc_iso(value) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _component(status, message, details=None, latency_ms=None, issue=None):
    result = {
        "status": status,
        "message": message,
        "details": details or {},
    }
    if latency_ms is not None:
        result["latency_ms"] = latency_ms
    if issue is not None:
        result["_issue"] = issue
    return result


def _worst(*statuses: str) -> str:
    return max(statuses, key=lambda s: _SEVERITY.get(s, 0))


# ----------------------------------------------------------------------
# DB head (code side) — parsed from the migration files, cached
# ----------------------------------------------------------------------

def _code_migration_heads() -> set[str] | None:
    try:
        versions_dir = Path(__file__).resolve().parents[3] / "migrations" / "versions"
        revisions: dict[str, list[str]] = {}
        for path in versions_dir.glob("*.py"):
            text_content = path.read_text()
            match = _MIGRATION_REVISION_RE.search(text_content)
            if not match:
                continue
            down_match = _MIGRATION_DOWN_RE.search(text_content)
            downs: list[str] = []
            if down_match:
                if down_match.group(1):
                    downs = [down_match.group(1)]
                elif down_match.group(2):
                    downs = re.findall(r'"([^"]+)"', down_match.group(2))
            revisions[match.group(1)] = downs
        used = {down for downs in revisions.values() for down in downs}
        return {rev for rev in revisions if rev not in used} or None
    except Exception:
        logger.exception("Could not read migration heads from disk.")
        return None


_CODE_HEADS: set[str] | None = None
_CODE_HEADS_LOADED = False


def _get_code_heads() -> set[str] | None:
    global _CODE_HEADS, _CODE_HEADS_LOADED
    if not _CODE_HEADS_LOADED:
        _CODE_HEADS = _code_migration_heads()
        _CODE_HEADS_LOADED = True
    return _CODE_HEADS


async def _check_db(db: AsyncSession) -> dict:
    started = time.monotonic()
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("System health: database connection failed.")
        return _component(
            FAIL,
            "The database could not be reached.",
            {"connection": "failed"},
            issue=("db_connection", "unreachable", "reachable"),
        )
    latency_ms = int((time.monotonic() - started) * 1000)

    db_version = None
    try:
        db_version = (
            await db.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one_or_none()
    except Exception:
        await db.rollback()
        db_version = None

    code_heads = _get_code_heads()
    head_match = None
    if db_version is not None and code_heads:
        head_match = db_version in code_heads

    pool_info = None
    pool_pct = None
    try:
        pool = engine.pool
        checked_out = pool.checkedout()
        size = pool.size()
        if size:
            pool_pct = round(checked_out * 100 / size)
            pool_info = {
                "checked_out": checked_out,
                "size": size,
                "checked_out_pct": pool_pct,
            }
    except Exception:
        pool_info = None

    status = OK
    message = "The database is responding normally."
    issue = None
    if latency_ms > 1000:
        status, message = FAIL, "The database is responding very slowly."
        issue = ("db_latency_ms", latency_ms, "<= 1000 ms")
    elif latency_ms >= 200:
        status, message = WARNING, "The database is responding slowly."
        issue = ("db_latency_ms", latency_ms, "< 200 ms")
    if head_match is False and status != FAIL:
        status, message = WARNING, "The database version does not match the latest update yet."
        issue = ("migration_head_match", db_version, "code head")
    if pool_pct is not None and pool_pct >= 100:
        status, message = FAIL, "The database connection pool is full."
        issue = ("pool_checked_out_pct", pool_pct, "< 100%")
    elif pool_pct is not None and pool_pct >= 80 and status == OK:
        status, message = WARNING, "The database connection pool is almost full."
        issue = ("pool_checked_out_pct", pool_pct, "< 80%")

    return _component(
        status,
        message,
        {
            "migration_head_match": head_match,
            "db_version": db_version,
            "pool": pool_info,
        },
        latency_ms=latency_ms,
        issue=issue,
    )


async def _table_columns(db: AsyncSession, table: str) -> set[str] | None:
    """Column names via the inspector (works on Postgres and SQLite)."""
    try:
        def _cols(sync_conn):
            return {c["name"] for c in sa_inspect(sync_conn).get_columns(table)}

        return await db.run_sync(_cols)
    except Exception:
        await db.rollback()
        return None


async def _check_data_integrity(db: AsyncSession) -> dict:
    from app.modules.kpi.models import KPIPeriod, KPIRecord
    from app.modules.tasks.models.task import Task

    failed: list[str] = []
    skipped: list[str] = []

    record_cols = await _table_columns(db, KPIRecord.__tablename__)
    if record_cols is not None and "employee_name" in record_cols:
        found = await db.execute(
            select(KPIRecord.id).where(
                KPIRecord.user_id.is_(None),
                KPIRecord.employee_name.is_(None),
            ).limit(1)
        )
        if found.scalar_one_or_none() is not None:
            failed.append("departed_records_missing_name")
    else:
        skipped.append("departed_records_missing_name")

    task_cols = await _table_columns(db, Task.__tablename__)
    if task_cols is not None and "owner_name" in task_cols:
        found = await db.execute(
            select(Task.id).where(
                Task.owner_id.is_(None),
                Task.owner_name.is_(None),
            ).limit(1)
        )
        if found.scalar_one_or_none() is not None:
            failed.append("departed_tasks_missing_owner")
    else:
        skipped.append("departed_tasks_missing_owner")

    found = await db.execute(
        select(KPIPeriod.id).where(
            KPIPeriod.start_date > KPIPeriod.end_date
        ).limit(1)
    )
    if found.scalar_one_or_none() is not None:
        failed.append("invalid_period_dates")

    if failed:
        return _component(
            FAIL,
            "Some saved records are missing the information they should carry.",
            {"failed_probes": failed, "skipped_probes": skipped},
            issue=("failed_probes", failed, "[]"),
        )
    return _component(
        OK,
        "Saved records look complete.",
        {"failed_probes": [], "skipped_probes": skipped},
    )


async def _check_data_flow(db: AsyncSession, now: datetime) -> dict:
    from app.modules.auth.models import LoginHistory
    from app.modules.kpi.models import KPIRecord
    from app.modules.tasks.models.submission_batch import SubmissionBatch
    from app.modules.tasks.models.task import Task
    from app.models.audit_log import AuditLog

    async def _counts(since: datetime) -> dict:
        result = {}
        for name, model in (
            ("kpi_records", KPIRecord),
            ("tasks", Task),
            ("submission_batches", SubmissionBatch),
            ("audit_logs", AuditLog),
        ):
            result[name] = (
                await db.execute(
                    select(func.count()).select_from(model).where(
                        model.created_at >= since
                    )
                )
            ).scalar() or 0
        result["total"] = sum(result.values())
        return result

    last_1h = await _counts(now - timedelta(hours=1))
    last_24h = await _counts(now - timedelta(hours=24))
    successful_logins_24h = (
        await db.execute(
            select(func.count()).select_from(LoginHistory).where(
                LoginHistory.is_success.is_(True),
                LoginHistory.attempted_at >= now - timedelta(hours=24),
            )
        )
    ).scalar() or 0

    if last_24h["total"] == 0 and successful_logins_24h > 0:
        return _component(
            WARNING,
            "People signed in during the last day, but no new work was saved.",
            {"last_1h": last_1h, "last_24h": last_24h},
            issue=("flow_24h_total", 0, "> 0 when people are signing in"),
        )
    return _component(
        OK,
        "New work is flowing in normally.",
        {"last_1h": last_1h, "last_24h": last_24h},
    )


async def _check_security(
    db: AsyncSession, now: datetime, active_sessions: int
) -> dict:
    from app.modules.auth.models import LoginHistory, UnlockRequest
    from app.modules.users.models import User

    failed_logins_1h = (
        await db.execute(
            select(func.count()).select_from(LoginHistory).where(
                LoginHistory.is_success.is_(False),
                LoginHistory.attempted_at >= now - timedelta(hours=1),
            )
        )
    ).scalar() or 0
    locked_accounts = (
        await db.execute(
            select(func.count()).select_from(User).where(
                (User.is_locked.is_(True)) | (User.locked_until > now)
            )
        )
    ).scalar() or 0
    pending_unlocks = (
        await db.execute(
            select(func.count()).select_from(UnlockRequest).where(
                UnlockRequest.status == "PENDING"
            )
        )
    ).scalar() or 0

    checks = []
    if failed_logins_1h >= 50:
        checks.append((FAIL, "failed_logins_1h", failed_logins_1h, "< 50"))
    elif failed_logins_1h >= 10:
        checks.append((WARNING, "failed_logins_1h", failed_logins_1h, "< 10"))
    if locked_accounts >= 5:
        checks.append((FAIL, "locked_accounts", locked_accounts, "< 5"))
    elif locked_accounts >= 1:
        checks.append((WARNING, "locked_accounts", locked_accounts, "0"))
    if pending_unlocks >= 3:
        checks.append((FAIL, "pending_unlocks", pending_unlocks, "< 3"))
    elif pending_unlocks >= 1:
        checks.append((WARNING, "pending_unlocks", pending_unlocks, "0"))

    if checks:
        worst = max(checks, key=lambda c: _SEVERITY[c[0]])
        status, check, value, threshold = worst
        if status == FAIL:
            message = "Unusual sign-in activity needs attention right away."
        else:
            message = "Some sign-in activity needs a look."
        issue = (check, value, threshold)
    else:
        status, message, issue = OK, "Sign-in activity looks normal.", None

    return _component(
        status,
        message,
        {
            "failed_logins_1h": failed_logins_1h,
            "locked_accounts": locked_accounts,
            "pending_unlocks": pending_unlocks,
            "active_sessions": active_sessions,
        },
        issue=issue,
    )


async def _check_user_activity(
    db: AsyncSession, now: datetime, active_sessions: int
) -> dict:
    from app.modules.auth.models import LoginHistory

    logins_24h = (
        await db.execute(
            select(func.count()).select_from(LoginHistory).where(
                LoginHistory.is_success.is_(True),
                LoginHistory.attempted_at >= now - timedelta(hours=24),
            )
        )
    ).scalar() or 0
    active_users_24h = (
        await db.execute(
            select(func.count(func.distinct(LoginHistory.user_id))).where(
                LoginHistory.is_success.is_(True),
                LoginHistory.attempted_at >= now - timedelta(hours=24),
            )
        )
    ).scalar() or 0
    last_login = (
        await db.execute(
            select(func.max(LoginHistory.attempted_at)).where(
                LoginHistory.is_success.is_(True)
            )
        )
    ).scalar_one_or_none()

    if last_login is None or last_login < now - timedelta(days=7):
        return _component(
            WARNING,
            "Nobody has signed in successfully for over a week.",
            {
                "active_sessions": active_sessions,
                "logins_24h": logins_24h,
                "active_users_24h": active_users_24h,
                "last_login_at": _as_utc_iso(last_login),
            },
            issue=("last_login_at", _as_utc_iso(last_login), "within 7 days"),
        )
    return _component(
        OK,
        "People are using the system.",
        {
            "active_sessions": active_sessions,
            "logins_24h": logins_24h,
            "active_users_24h": active_users_24h,
            "last_login_at": _as_utc_iso(last_login),
        },
    )


async def _check_minio() -> dict:
    if not settings.USE_S3:
        return _component(
            DISABLED,
            "File storage is turned off in settings.",
            {"bucket": settings.S3_BUCKET_NAME, "probe": "disabled"},
        )
    if not all((settings.S3_ENDPOINT, settings.S3_ACCESS_KEY, settings.S3_SECRET_KEY)):
        return _component(
            FAIL,
            "File storage is turned on but its connection details are missing.",
            {"bucket": settings.S3_BUCKET_NAME, "probe": "not_configured"},
            issue=("minio_configured", False, True),
        )

    import boto3
    from botocore.config import Config as BotoConfig

    def _probe() -> tuple[str, int | None, str | None]:
        started = time.monotonic()
        key = f"health/probe-{int(time.time())}.txt"
        client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            config=BotoConfig(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1}),
        )
        try:
            client.head_bucket(Bucket=settings.S3_BUCKET_NAME)
        except Exception:
            return FAIL, None, "head_failed"
        try:
            client.put_object(Bucket=settings.S3_BUCKET_NAME, Key=key, Body=b"ok")
            client.get_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
            latency_ms = int((time.monotonic() - started) * 1000)
            if latency_ms >= 2000:
                return WARNING, latency_ms, "slow"
            return OK, latency_ms, "ok"
        except Exception:
            return WARNING, None, "probe_failed"
        finally:
            try:
                client.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
            except Exception:
                pass

    try:
        status, latency_ms, probe = await asyncio.to_thread(_probe)
    except Exception:
        logger.exception("System health: MinIO probe crashed.")
        return _component(
            FAIL,
            "File storage could not be reached.",
            {"bucket": settings.S3_BUCKET_NAME, "probe": "failed"},
            issue=("minio_head", "failed", "ok"),
        )
    messages = {
        OK: "File storage is responding normally.",
        WARNING: "File storage is responding, but slowly or unreliably.",
        FAIL: "File storage could not be reached.",
    }
    issue = None
    if status == WARNING:
        issue = ("minio_probe", probe, "ok in under 2 seconds")
    elif status == FAIL:
        issue = ("minio_head", probe, "ok")
    return _component(
        status,
        messages[status],
        {"bucket": settings.S3_BUCKET_NAME, "probe": probe},
        latency_ms=latency_ms,
        issue=issue,
    )


async def _check_redis() -> dict:
    if not settings.REDIS_URL:
        return _component(
            DISABLED,
            "The cache is not configured, so the system runs without it.",
            {"probe": "disabled"},
        )
    from redis.asyncio import from_url as redis_from_url

    started = time.monotonic()
    client = None
    try:
        client = redis_from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
        )
        await client.ping()
    except Exception:
        return _component(
            FAIL,
            "The cache could not be reached.",
            {"probe": "failed"},
            issue=("redis_ping", "failed", "PONG"),
        )
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
    latency_ms = int((time.monotonic() - started) * 1000)
    if latency_ms >= 200:
        return _component(
            WARNING,
            "The cache is responding slowly.",
            {"probe": "ok"},
            latency_ms=latency_ms,
            issue=("redis_latency_ms", latency_ms, "< 200 ms"),
        )
    if latency_ms >= 50:
        return _component(
            WARNING,
            "The cache is responding a little slowly.",
            {"probe": "ok"},
            latency_ms=latency_ms,
            issue=("redis_latency_ms", latency_ms, "< 50 ms"),
        )
    return _component(
        OK,
        "The cache is responding normally.",
        {"probe": "ok"},
        latency_ms=latency_ms,
    )


# ----------------------------------------------------------------------
# Collection, cache, and status-change-only logging
# ----------------------------------------------------------------------

def reset_health_cache() -> None:
    _CACHE["at"] = 0.0
    _CACHE["payload"] = None


def reset_health_log_state() -> None:
    _LAST_HEALTH_LOG.clear()


async def _log_status_changes(payload: dict) -> None:
    now_mono = time.monotonic()
    for name, component in payload["components"].items():
        status = component["status"]
        if status == DISABLED:
            # Turned off is not a failure — nothing to log, ever.
            continue
        previous = _LAST_HEALTH_LOG.get(name)
        changed = previous is None or previous[0] != status
        stale = (
            previous is not None
            and status in (WARNING, FAIL)
            and now_mono - previous[1] >= LOG_REPEAT_SECONDS
        )
        if status == OK and not (previous and previous[0] != OK):
            if previous is None:
                _LAST_HEALTH_LOG[name] = (OK, now_mono)
            continue
        if not changed and not stale:
            continue
        level = (
            LogLevel.ERROR if status == FAIL
            else LogLevel.WARNING if status == WARNING
            else LogLevel.INFO
        )
        if status == OK:
            message = f"System health: {name.replace('_', ' ')} is back to normal."
        else:
            message = component["message"]
        issue = component.get("_issue")
        details = {
            "component": name,
            "status": status,
            "check": issue[0] if issue else "overall",
            "value": issue[1] if issue else None,
            "threshold": issue[2] if issue else None,
        }
        try:
            await record_system_log(level, "HEALTH_CHECK", message, details)
        except Exception:
            logger.exception("System health: could not write HEALTH_CHECK log.")
        _LAST_HEALTH_LOG[name] = (status, now_mono)


async def collect_system_health(db: AsyncSession) -> dict:
    """Full health payload, cached for 10 seconds (fresh checks log
    status changes; cached answers do not re-log)."""
    now_mono = time.monotonic()
    if (
        _CACHE["payload"] is not None
        and now_mono - _CACHE["at"] < CACHE_TTL_SECONDS
    ):
        return _CACHE["payload"]

    from app.modules.auth.models import AuthSession

    now = _now_naive()
    active_sessions = (
        await db.execute(
            select(func.count()).select_from(AuthSession).where(
                AuthSession.expires_at > now
            )
        )
    ).scalar() or 0

    components = {
        "db": await _check_db(db),
        "data_integrity": await _check_data_integrity(db),
        "data_flow": await _check_data_flow(db, now),
        "security": await _check_security(db, now, active_sessions),
        "user_activity": await _check_user_activity(db, now, active_sessions),
        "minio": await _check_minio(),
        "redis": await _check_redis(),
    }
    live = [c["status"] for c in components.values() if c["status"] != DISABLED]
    payload = {
        "overall": _worst(*live) if live else OK,
        "checked_at": datetime.now(UTC).isoformat(),
        "components": {
            name: {k: v for k, v in comp.items() if not k.startswith("_")}
            for name, comp in components.items()
        },
    }
    await _log_status_changes(
        {"components": components, "overall": payload["overall"]}
    )
    _CACHE["at"] = now_mono
    _CACHE["payload"] = payload
    return payload

import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import AsyncSessionLocal
from app.models.system import LogLevel, SystemEventLog

logger = logging.getLogger(__name__)

# Retention (Zenn's numbers, 2026-10-23): system_event_logs holds at
# most 1,000 rows. Once the cap is reached, the oldest rows are
# pruned down to 900 in one pass; the table then refills and the
# cycle repeats. This applies ONLY to system_event_logs — audit_logs
# are a compliance trail with their own retention module
# (app.modules.system.audit_log_retention) and are never touched here.
MAX_SYSTEM_EVENT_LOGS = 1000
SYSTEM_LOG_PRUNE_TARGET = 900


async def prune_system_event_logs(
    db: AsyncSession,
    *,
    max_rows: int = MAX_SYSTEM_EVENT_LOGS,
    target_rows: int = SYSTEM_LOG_PRUNE_TARGET,
) -> int:
    """Delete the oldest rows beyond the cap; return how many.

    One DELETE statement over an oldest-first id subquery: safe to
    run concurrently (a second run observes the already-pruned count
    and stops) and idempotent (at or below the target it deletes
    nothing). Ordering is (created_at, id) so ties are deterministic.
    """
    total = (
        await db.execute(
            select(func.count(SystemEventLog.id)).where(
                SystemEventLog.is_deleted.is_(False)
            )
        )
    ).scalar_one()
    if total < max_rows:
        return 0
    excess = total - target_rows
    oldest_ids = (
        select(SystemEventLog.id)
        .where(SystemEventLog.is_deleted.is_(False))
        .order_by(SystemEventLog.created_at.asc(), SystemEventLog.id.asc())
        .limit(excess)
    )
    result = await db.execute(
        delete(SystemEventLog).where(SystemEventLog.id.in_(oldest_ids))
    )
    await db.commit()
    return result.rowcount or 0
_SENSITIVE_KEYS = ("password", "token", "secret", "authorization", "cookie", "api_key")


def _safe_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if any(term in str(key).lower() for term in _SENSITIVE_KEYS)
            else _safe_metadata(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_metadata(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


async def record_system_log(
    level: LogLevel,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    safe_details = _safe_metadata(details or {})
    if not safe_details:
        safe_details = {
            "component": event_type.split("_", 1)[0].lower(),
            "event": event_type.lower(),
        }
    getattr(logger, level.value.lower(), logger.info)(message)
    try:
        async with AsyncSessionLocal() as db:
            db.add(SystemEventLog(
                event_type=event_type,
                level=level,
                message=message,
                details=safe_details,
            ))
            await db.commit()
            # Retention: best-effort — a prune failure must never
            # break logging itself.
            try:
                await prune_system_event_logs(db)
            except Exception:
                logger.exception("System log retention prune failed")
    except Exception:
        logger.exception("Failed to persist system log")

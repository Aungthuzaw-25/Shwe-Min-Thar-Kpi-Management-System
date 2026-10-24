"""Audit log retention — configurable from System Settings.

Zenn's decision (2026-10-23): audit logs are kept FOREVER by
default, but the keep-period must be changeable from a setting —
no code change — if he ever changes his mind.

Contract:
- Setting key ``audit_retention_years``, value
  ``{"retention_years": N}`` where N is 0, 1, 2, or 5.
  **0 = keep forever (the default; also what a missing setting
  means).**
- Only a Super Admin can change it (enforced in the settings API,
  like the other guarded settings); every change is written to
  the audit log by the settings endpoints (old value -> new value).
- The daily scheduler cleanup enforces it: with 0 it does
  nothing; with a finite period it deletes audit rows older than
  the period, in batches, and the purge itself leaves an
  AUDIT_LOG_PURGE audit row (which setting, how many rows).
- Fail-safe: an unreadable/invalid stored value is treated as 0
  (keep) and logged — a configuration mistake must never delete
  the permanent record.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.system import SystemSetting

logger = logging.getLogger(__name__)

AUDIT_RETENTION_YEARS_KEY = "audit_retention_years"
AUDIT_RETENTION_YEARS_OPTIONS = (0, 1, 2, 5)
AUDIT_LOG_DELETE_BATCH_SIZE = 1000
DAYS_PER_YEAR = 365


def normalize_audit_retention_years(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {"retention_years"}:
        raise ValueError("Audit retention must contain only retention_years.")
    years = value["retention_years"]
    if (
        isinstance(years, bool)
        or not isinstance(years, int)
        or years not in AUDIT_RETENTION_YEARS_OPTIONS
    ):
        raise ValueError("Audit retention years must be 0 (forever), 1, 2, or 5.")
    return {"retention_years": years}


async def get_audit_retention_years(db: AsyncSession) -> int:
    """The configured keep-period in years; 0 (forever) by default."""
    setting = (
        await db.execute(
            select(SystemSetting).where(
                SystemSetting.key == AUDIT_RETENTION_YEARS_KEY
            )
        )
    ).scalar_one_or_none()
    if setting is None:
        return 0
    try:
        return normalize_audit_retention_years(setting.value)["retention_years"]
    except ValueError:
        logger.warning(
            "Stored audit_retention_years value is invalid (%r); "
            "treating it as 0 (keep forever).",
            setting.value,
        )
        return 0


async def delete_expired_audit_logs(
    db: AsyncSession,
    retention_years: int,
    *,
    batch_size: int = AUDIT_LOG_DELETE_BATCH_SIZE,
) -> tuple[int, datetime]:
    """Delete audit rows older than the period, in batches.

    Returns (deleted_count, cutoff). Each batch commits separately,
    so a huge backlog drains incrementally and a re-run simply
    finds nothing (idempotent).
    """
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        days=DAYS_PER_YEAR * retention_years
    )
    deleted_count = 0
    while True:
        expired_ids = (
            await db.execute(
                select(AuditLog.id)
                .where(AuditLog.created_at < cutoff)
                .order_by(AuditLog.created_at, AuditLog.id)
                .limit(batch_size)
            )
        ).scalars().all()
        if not expired_ids:
            break
        result = await db.execute(
            delete(AuditLog).where(AuditLog.id.in_(expired_ids))
        )
        deleted_count += result.rowcount or 0
        await db.commit()
    return deleted_count, cutoff


async def run_audit_log_retention_cleanup() -> int:
    """Daily scheduler entry point. 0 years = keep forever = no-op."""
    async with AsyncSessionLocal() as db:
        years = await get_audit_retention_years(db)
        if years == 0:
            return 0
        deleted_count, cutoff = await delete_expired_audit_logs(db, years)
        if deleted_count:
            db.add(
                AuditLog(
                    user_id=None,
                    action="AUDIT_LOG_PURGE",
                    table_name="audit_logs",
                    new_value={
                        "retention_years": years,
                        "deleted_count": deleted_count,
                        "cutoff": cutoff.isoformat(),
                    },
                )
            )
            await db.commit()
            logger.info(
                "Audit log purge: deleted %s rows older than %s year(s).",
                deleted_count,
                years,
            )
        return deleted_count

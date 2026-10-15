from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.system import SystemSetting

AUDIT_LOG_RETENTION_KEY = "audit_log_retention"
AUDIT_LOG_RETENTION_OPTIONS = (None, 30, 90, 180, 365)
AUDIT_LOG_DELETE_BATCH_SIZE = 1000


def normalize_audit_log_retention(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {"retention_days"}:
        raise ValueError("Audit log retention must contain only retention_days.")
    retention_days = value["retention_days"]
    if retention_days is not None and (
        isinstance(retention_days, bool)
        or not isinstance(retention_days, int)
        or retention_days not in AUDIT_LOG_RETENTION_OPTIONS
    ):
        raise ValueError("Audit log retention must be None, 30, 90, 180, or 365 days.")
    return {"retention_days": retention_days}


async def delete_expired_audit_logs(
    db: AsyncSession,
    retention_days: int,
    *,
    batch_size: int = AUDIT_LOG_DELETE_BATCH_SIZE,
) -> int:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_days)
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
    return deleted_count


async def run_audit_log_retention_cleanup() -> int:
    async with AsyncSessionLocal() as db:
        setting = (
            await db.execute(
                select(SystemSetting).where(
                    SystemSetting.key == AUDIT_LOG_RETENTION_KEY
                )
            )
        ).scalar_one_or_none()
        if setting is None:
            return 0

        retention = normalize_audit_log_retention(setting.value)
        retention_days = retention["retention_days"]
        if retention_days is None:
            return 0
        deleted_count = await delete_expired_audit_logs(db, retention_days)
        await db.commit()
        return deleted_count

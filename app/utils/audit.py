"""
Standardized Audit Trail Utility
"""
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)

async def log_audit_trail(
    db: AsyncSession,
    user_id: UUID | None,
    action: str,
    table_name: str,
    record_id: str | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    ip_address: str | None = "127.0.0.1",
    *,
    commit: bool = True,
) -> AuditLog | None:
    """စနစ်တွင်း အပြောင်းအလဲများကို audit_logs table တွင် တပြေးညီ မှတ်တမ်းတင်ရန်"""
    try:
        log_entry = AuditLog(
            user_id=user_id,
            action=action,
            table_name=table_name,
            record_id=str(record_id) if record_id else None,
            old_value=old_value,
            new_value=new_value,
            ip_address=ip_address
        )
        db.add(log_entry)
        if commit:
            await db.commit()
            await db.refresh(log_entry)
        return log_entry
    except Exception:
        await db.rollback()
        logger.exception("Failed to record audit trail for %s", table_name)
        return None
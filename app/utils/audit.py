"""
Standardized Audit Trail Utility
"""
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import Optional, Dict, Any
from app.models.audit_log import AuditLog
import logging

logger = logging.getLogger(__name__)

async def log_audit_trail(
    db: AsyncSession,
    user_id: Optional[UUID],
    action: str,
    table_name: str,
    record_id: Optional[str] = None,
    old_value: Optional[Dict[str, Any]] = None,
    new_value: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = "127.0.0.1",
    *,
    commit: bool = True,
) -> Optional[AuditLog]:
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
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to record audit trail for {table_name}: {e}")
        return None
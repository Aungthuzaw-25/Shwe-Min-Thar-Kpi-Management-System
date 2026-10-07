from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.modules.kpi.models import KPIPeriod, KPIPeriodStatus, KPIRecord


async def change_period_status(
    db: AsyncSession, 
    period_id: UUID, 
    new_status: KPIPeriodStatus, 
    user_id: UUID,
    reason: str = None
) -> KPIPeriod:
    
    period = await db.get(KPIPeriod, period_id)
    if not period:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="KPI Period not found in the system.")

    old_status = period.status

    # 🌟 Prevent redundant updates
    if old_status == new_status:
        return period

    # 🌟 Security & Auditing: Force a reason if unlocking/reopening a closed period
    if new_status == KPIPeriodStatus.OPEN and old_status in [KPIPeriodStatus.FINALIZED, KPIPeriodStatus.LOCKED]:
        if not reason or len(reason.strip()) < 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="A valid, detailed reason is strictly required to reopen a finalized or locked period."
            )
        
        # 🌟 FIX: Removed json.dumps() to prevent SQLAlchemy JSON column crash
        audit = AuditLog(
            user_id=user_id,
            action="REOPEN_PERIOD",
            table_name="kpi_periods",
            record_id=str(period_id),
            old_value={"status": old_status.value},
            new_value={"status": new_status.value, "reason": reason},
            ip_address="System_Internal" 
        )
        db.add(audit)

    period.status = new_status
    await db.commit()
    await db.refresh(period)
    return period

async def check_period_editable(db: AsyncSession, period_id: UUID):
    """
    Dependency / Helper to block any edits on KPI Records if their period is LOCKED.
    Call this before saving any task or score update.
    """
    if not period_id:
        return 
        
    period = await db.get(KPIPeriod, period_id)
    if period and period.status in [KPIPeriodStatus.FINALIZED, KPIPeriodStatus.LOCKED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Operation rejected. Cannot modify records inside a FINALIZED or LOCKED evaluation period."
        )


def ensure_kpi_record_editable(record: KPIRecord) -> None:
    """Reject direct KPI mutations for authoritative or cancelled periods.

    Records without a linked period retain the legacy editable behavior because
    there is no period lifecycle state to evaluate.
    """
    period = getattr(record, "period", None)
    if period and period.status in {
        KPIPeriodStatus.FINALIZED,
        KPIPeriodStatus.LOCKED,
        KPIPeriodStatus.CANCELLED,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Finalized, locked, or cancelled KPI records require the correction workflow.",
        )
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.modules.kpi.models import ActionPlan, KPIRecord, KPIStatus


async def require_action_plan_check(db: AsyncSession, kpi_record_id: UUID):
    """
    Check if a KPI Record requires an action plan and if it has one.
    This can be called before Finalizing a KPI period.
    """
    record = await db.get(KPIRecord, kpi_record_id)
    if not record:
        raise HTTPException(status_code=404, detail="KPI Record not found")

    # If score is Poor or Fair, an Action Plan is strictly required
    if record.status_bucket in [KPIStatus.POOR, KPIStatus.FAIR]:
        stmt = select(ActionPlan).where(
            ActionPlan.kpi_record_id == kpi_record_id,
            ActionPlan.is_deleted == False
        )
        result = await db.execute(stmt)
        action_plan = result.scalars().first()

        if not action_plan:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action Plan is strictly required for {record.status_bucket.value} performance. Please submit an action plan before finalizing."
            )
    return True
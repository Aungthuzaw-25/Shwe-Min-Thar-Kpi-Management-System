from datetime import UTC, datetime
from uuid import UUID

from fastapi import (  # 🌟 FIX: Added Query for Pagination
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import (
    RoleChecker,
    enforce_permission,
    enforce_user_scope,
    get_current_user,
)
from app.database.connection import get_db
from app.modules.departments.models import UserDepartment
from app.modules.kpi.action_plan_schemas import ActionPlanCreate, ActionPlanReview
from app.modules.kpi.models import (
    ActionPlan,
    ActionPlanStatus,
    KPIPeriodStatus,
    KPIRecord,
    KPIStatus,
)
from app.modules.users.models import User

router = APIRouter(prefix="/action-plans", tags=["KPI Action Plans"])
KPI_RECORD_NOT_FOUND = "KPI Record not found."

# Manager များ၊ HR နှင့် Admin များသာ Review လုပ်ခွင့်ရှိသည်
reviewer_role = RoleChecker(["Super Admin", "Admin", "HR", "Department Head"])


async def _enforce_kpi_record_scope(
    db: AsyncSession, current_user: User, target_user_id
) -> None:
    try:
        await enforce_user_scope(db, current_user, target_user_id)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_403_FORBIDDEN:
            raise
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=KPI_RECORD_NOT_FOUND,
        ) from exc


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Action plan is not valid for this KPI record"},
        403: {"description": "User cannot submit an action plan for this KPI record"},
        404: {"description": "KPI record not found"},
        409: {"description": "Finalized KPI periods require an explicit correction workflow"},
    },
)
async def submit_action_plan(
    payload: ActionPlanCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """ဝန်ထမ်းမှ မိမိ၏ ရမှတ်နိမ့်သော KPI အတွက် Action Plan တင်ပြရန်"""
    enforce_permission(current_user, "kpi.create")
    
    # 🌟 SECURITY: Check if record exists AND belongs to the user
    record = (await db.execute(
        select(KPIRecord).options(
            selectinload(KPIRecord.period)
        ).where(KPIRecord.id == payload.kpi_record_id, KPIRecord.is_deleted == False)
    )).scalars().first()
    if not record:
        raise HTTPException(status_code=404, detail=KPI_RECORD_NOT_FOUND)
        
    await _enforce_kpi_record_scope(db, current_user, record.user_id)
    if record.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the KPI owner can submit an action plan.")
    if record.period and record.period.status in {KPIPeriodStatus.FINALIZED, KPIPeriodStatus.LOCKED, KPIPeriodStatus.CANCELLED}:
        raise HTTPException(status_code=409, detail="Finalized KPI periods require an explicit correction workflow.")
    if record.status_bucket not in {KPIStatus.POOR, KPIStatus.FAIR}:
        raise HTTPException(status_code=400, detail="An action plan is only required for low KPI scores.")

    # Action Plan တင်ပြီးသား ရှိ/မရှိ စစ်ဆေးရန်
    stmt = select(ActionPlan).where(ActionPlan.kpi_record_id == payload.kpi_record_id)
    result = await db.execute(stmt)
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Action Plan already exists for this KPI Record.")

    new_plan = ActionPlan(
        kpi_record_id=payload.kpi_record_id,
        user_id=current_user.id,
        description=payload.description,
        target_completion_date=payload.target_completion_date.replace(tzinfo=None),
        status=ActionPlanStatus.SUBMITTED
    )
    db.add(new_plan)
    await db.commit()
    await db.refresh(new_plan)
    return new_plan

# 🌟 PERFORMANCE FIX: Added Pagination to prevent Memory Exhaustion
@router.get(
    "/all",
    responses={403: {"description": "Not authorized to view action plans"}},
)
async def get_all_action_plans(
    skip: int = Query(0, ge=0), 
    limit: int = Query(100, gt=0, le=500), 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(reviewer_role)
):
    """System အတွင်းရှိ Action Plan အားလုံးကို ကြည့်ရှုရန် (Manager/Admin View)"""
    enforce_permission(current_user, "kpi.view")
    stmt = select(ActionPlan).where(ActionPlan.is_deleted.is_(False))
    if current_user.role.name not in {"Super Admin", "HR"}:
        stmt = stmt.where(ActionPlan.user_id.in_(
            select(UserDepartment.user_id).where(
                UserDepartment.department_id.in_(
                    select(UserDepartment.department_id).where(
                        UserDepartment.user_id == current_user.id,
                        UserDepartment.is_deleted.is_(False),
                    )
                ),
                UserDepartment.is_deleted.is_(False),
            )
        ))
    stmt = stmt.order_by(ActionPlan.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()

@router.get(
    "/record/{kpi_record_id}",
    responses={
        403: {"description": "Not authorized to view this KPI record"},
        404: {"description": "KPI record or action plan not found"},
    },
)
async def get_action_plan(
    kpi_record_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """KPI Record တစ်ခုနှင့် သက်ဆိုင်သော Action Plan ကို ကြည့်ရှုရန်"""
    enforce_permission(current_user, "kpi.view")
    
    # 🌟 SECURITY: Prevent IDOR (Data Stealing). Users cannot read other's PIPs.
    record = (await db.execute(
        select(KPIRecord).where(
            KPIRecord.id == kpi_record_id,
            KPIRecord.is_deleted.is_(False),
        )
    )).scalars().first()
    if not record:
        raise HTTPException(status_code=404, detail=KPI_RECORD_NOT_FOUND)
        
    await _enforce_kpi_record_scope(db, current_user, record.user_id)

    stmt = select(ActionPlan).where(ActionPlan.kpi_record_id == kpi_record_id, ActionPlan.is_deleted == False)
    result = await db.execute(stmt)
    plan = result.scalars().first()
    
    if not plan:
        raise HTTPException(status_code=404, detail="Action Plan not found for this record.")
    return plan

@router.put(
    "/{plan_id}/review",
    dependencies=[Depends(reviewer_role)],
    responses={
        400: {"description": "Invalid action plan status transition"},
        403: {"description": "Not authorized to review this action plan"},
        404: {"description": "Action plan not found"},
        409: {"description": "Finalized KPI periods require an explicit correction workflow"},
    },
)
async def review_action_plan(
    plan_id: UUID,
    payload: ActionPlanReview,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Manager သို့မဟုတ် Admin မှ တင်ပြလာသော Action Plan အား Approve/Reject လုပ်ရန်"""
    enforce_permission(current_user, "kpi.edit")
    plan = (await db.execute(
        select(ActionPlan).options(selectinload(ActionPlan.record)).where(
            ActionPlan.id == plan_id, ActionPlan.is_deleted == False
        )
    )).scalars().first()
    if not plan:
        raise HTTPException(status_code=404, detail="Action Plan not found.")
    await enforce_user_scope(db, current_user, plan.user_id)
    if plan.record and plan.record.period and plan.record.period.status in {
        KPIPeriodStatus.FINALIZED, KPIPeriodStatus.LOCKED, KPIPeriodStatus.CANCELLED
    }:
        raise HTTPException(status_code=409, detail="Finalized KPI periods require an explicit correction workflow.")

    valid_transitions = {
        ActionPlanStatus.SUBMITTED: {ActionPlanStatus.APPROVED, ActionPlanStatus.REJECTED},
        ActionPlanStatus.APPROVED: {ActionPlanStatus.COMPLETED},
        ActionPlanStatus.REJECTED: {ActionPlanStatus.SUBMITTED},
    }
    allowed = valid_transitions.get(plan.status, set())
    if payload.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action plan transition: {plan.status.value} -> {payload.status.value}.",
        )
    plan.status = payload.status
    plan.manager_feedback = payload.manager_feedback
    plan.reviewed_by_id = current_user.id
    plan.reviewed_at = datetime.now(UTC).replace(tzinfo=None)

    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan
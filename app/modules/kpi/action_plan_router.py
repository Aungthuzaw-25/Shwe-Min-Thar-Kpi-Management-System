from fastapi import APIRouter, Depends, HTTPException, status, Query # 🌟 FIX: Added Query for Pagination
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
from uuid import UUID
from datetime import datetime, timezone

from app.database.connection import get_db
from app.core.dependencies import RoleChecker, get_current_user, enforce_permission
from app.modules.kpi.models import ActionPlan, ActionPlanStatus, KPIRecord
from app.modules.kpi.action_plan_schemas import ActionPlanCreate, ActionPlanReview
from app.modules.users.models import User

router = APIRouter(prefix="/action-plans", tags=["KPI Action Plans"])

# Manager များ၊ HR နှင့် Admin များသာ Review လုပ်ခွင့်ရှိသည်
reviewer_role = RoleChecker(["Super Admin", "Admin", "HR", "Department Head"])

@router.post("/", status_code=status.HTTP_201_CREATED)
async def submit_action_plan(
    payload: ActionPlanCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """ဝန်ထမ်းမှ မိမိ၏ ရမှတ်နိမ့်သော KPI အတွက် Action Plan တင်ပြရန်"""
    enforce_permission(current_user, "kpi.create")
    
    # 🌟 SECURITY: Check if record exists AND belongs to the user
    record = await db.get(KPIRecord, payload.kpi_record_id)
    if not record:
        raise HTTPException(status_code=404, detail="KPI Record not found.")
        
    if str(record.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Security Exception: You cannot submit an action plan for another user's record.")

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
@router.get("/all")
async def get_all_action_plans(
    skip: int = Query(0, ge=0), 
    limit: int = Query(100, gt=0, le=500), 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(reviewer_role)
):
    """System အတွင်းရှိ Action Plan အားလုံးကို ကြည့်ရှုရန် (Manager/Admin View)"""
    enforce_permission(current_user, "kpi.view")
    stmt = select(ActionPlan).order_by(ActionPlan.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()

@router.get("/record/{kpi_record_id}")
async def get_action_plan(
    kpi_record_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """KPI Record တစ်ခုနှင့် သက်ဆိုင်သော Action Plan ကို ကြည့်ရှုရန်"""
    enforce_permission(current_user, "kpi.view")
    
    # 🌟 SECURITY: Prevent IDOR (Data Stealing). Users cannot read other's PIPs.
    record = await db.get(KPIRecord, kpi_record_id)
    if not record:
        raise HTTPException(status_code=404, detail="KPI Record not found.")
        
    if str(record.user_id) != str(current_user.id) and current_user.role.name not in ["Super Admin", "Admin", "HR", "Department Head"]:
        raise HTTPException(status_code=403, detail="Security Exception: Unauthorized access to sensitive action plan.")

    stmt = select(ActionPlan).where(ActionPlan.kpi_record_id == kpi_record_id)
    result = await db.execute(stmt)
    plan = result.scalars().first()
    
    if not plan:
        raise HTTPException(status_code=404, detail="Action Plan not found for this record.")
    return plan

@router.put("/{plan_id}/review", dependencies=[Depends(reviewer_role)])
async def review_action_plan(
    plan_id: UUID,
    payload: ActionPlanReview,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Manager သို့မဟုတ် Admin မှ တင်ပြလာသော Action Plan အား Approve/Reject လုပ်ရန်"""
    enforce_permission(current_user, "kpi.edit")
    plan = await db.get(ActionPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Action Plan not found.")

    plan.status = payload.status
    plan.manager_feedback = payload.manager_feedback
    plan.reviewed_by_id = current_user.id
    plan.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan
from fastapi import APIRouter, Depends, HTTPException, status, Query # 🌟 FIX: Added Query for Pagination
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
from uuid import UUID

from app.database.connection import get_db
# 🌟 RequireRole အစား RoleChecker သို့ ပြောင်းထားပါသည်
from app.core.dependencies import RoleChecker, get_current_user
from app.modules.kpi.models import KPIAssignment
from app.modules.kpi.assignment_schemas import KPIAssignmentCreate, KPIBulkAssignmentCreate
from app.modules.kpi import assignment_service
from app.modules.users.models import User

router = APIRouter(prefix="/assignments", tags=["KPI Assignments"])

# 🌟 Admin, HR နှင့် ဌာနမှူးများမှ Assignment လုပ်ခွင့်ရှိသည်
assigner_role = RoleChecker(["Super Admin", "Admin", "HR", "Department Head"])

@router.post("/assign", status_code=status.HTTP_201_CREATED, dependencies=[Depends(assigner_role)])
async def assign_single_kpi(
    payload: KPIAssignmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """ဝန်ထမ်းတစ်ဦးချင်းစီအတွက် KPI သတ်မှတ်ပေးရန်"""
    return await assignment_service.assign_kpi_to_user(db, payload.model_dump(), current_user.id)

@router.post("/bulk-assign", status_code=status.HTTP_201_CREATED, dependencies=[Depends(assigner_role)])
async def bulk_assign_kpi(
    payload: KPIBulkAssignmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """ဝန်ထမ်းများစွာအတွက် KPI တစ်ပြိုင်နက် သတ်မှတ်ပေးရန်"""
    return await assignment_service.bulk_assign_kpi(db, payload.model_dump(), current_user.id)

# 🌟 PERFORMANCE FIX: Added skip and limit to prevent memory exhaustion
@router.get("/user/{user_id}")
async def get_user_assignments(
    user_id: UUID,
    skip: int = Query(0, ge=0),    # 🌟 FIX: Pagination 
    limit: int = Query(100, gt=0), # 🌟 FIX: Pagination 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """ဝန်ထမ်းတစ်ဦး၏ KPI Assignment များကို ကြည့်ရှုရန်"""
    stmt = select(KPIAssignment).where(
        KPIAssignment.user_id == user_id, 
        KPIAssignment.is_deleted == False
    ).offset(skip).limit(limit)
    
    result = await db.execute(stmt)
    return result.scalars().all()
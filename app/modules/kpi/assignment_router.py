from uuid import UUID

from fastapi import (  # 🌟 FIX: Added Query for Pagination
    APIRouter,
    Depends,
    Query,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

# E3 (roles audit): assignment is gated by kpi.create OR department
# headship (require_kpi_manager) — the old RoleChecker named a
# "Department Head" role that never exists.
from app.core.dependencies import (
    enforce_department_scope,
    enforce_permission,
    enforce_user_scope,
    get_current_user,
    require_kpi_manager,
)
from app.database.connection import get_db
from app.modules.kpi import assignment_service
from app.modules.kpi.assignment_schemas import KPIAssignmentCreate, KPIBulkAssignmentCreate
from app.modules.kpi.models import KPIAssignment
from app.modules.users.models import User

router = APIRouter(prefix="/assignments", tags=["KPI Assignments"])

@router.post("/assign", status_code=status.HTTP_201_CREATED)
async def assign_single_kpi(
    payload: KPIAssignmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """ဝန်ထမ်းတစ်ဦးချင်းစီအတွက် KPI သတ်မှတ်ပေးရန်"""
    await require_kpi_manager(db, current_user, payload.department_id, "kpi.create")
    await enforce_department_scope(db, current_user, payload.department_id)
    return await assignment_service.assign_kpi_to_user(db, payload.model_dump(), current_user.id)

@router.post("/bulk-assign", status_code=status.HTTP_201_CREATED)
async def bulk_assign_kpi(
    payload: KPIBulkAssignmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """ဝန်ထမ်းများစွာအတွက် KPI တစ်ပြိုင်နက် သတ်မှတ်ပေးရန်"""
    await require_kpi_manager(db, current_user, payload.department_id, "kpi.create")
    await enforce_department_scope(db, current_user, payload.department_id)
    return await assignment_service.bulk_assign_kpi(db, payload.model_dump(), current_user.id)

# 🌟 PERFORMANCE FIX: Added skip and limit to prevent memory exhaustion
@router.get("/user/{user_id}")
async def get_user_assignments(
    user_id: UUID,
    skip: int = Query(0, ge=0),    # 🌟 FIX: Pagination 
    limit: int = Query(100, gt=0, le=500), # 🌟 FIX: Pagination 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """ဝန်ထမ်းတစ်ဦး၏ KPI Assignment များကို ကြည့်ရှုရန်"""
    enforce_permission(current_user, "kpi.view")
    await enforce_user_scope(db, current_user, user_id)
    stmt = select(KPIAssignment).where(
        KPIAssignment.user_id == user_id, 
        KPIAssignment.is_deleted == False
    ).order_by(KPIAssignment.created_at.desc(), KPIAssignment.id).offset(skip).limit(limit)
    
    result = await db.execute(stmt)
    return result.scalars().all()
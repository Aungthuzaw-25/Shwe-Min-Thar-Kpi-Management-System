from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
from uuid import UUID
from datetime import timezone

from app.database.connection import get_db
from app.core.dependencies import RoleChecker, get_current_user, enforce_permission
from app.modules.kpi.models import KPIPeriod, KPIPeriodStatus
from app.modules.kpi.period_schemas import KPIPeriodCreate, KPIPeriodResponse
from app.modules.kpi import period_service
from app.modules.users.models import User

router = APIRouter(prefix="/periods", tags=["KPI Periods"])

# 🌟 Security: Restrict access to HR and Admins
admin_only = RoleChecker(["Super Admin", "Admin", "HR"])

@router.post("/", response_model=KPIPeriodResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(admin_only)])
async def create_kpi_period(payload: KPIPeriodCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Initialize a new KPI evaluation cycle (e.g., Q3 2026 or August 2026)"""
    enforce_permission(current_user, "kpi.create")
    data = payload.model_dump()
    
    # Timezone အတိအကျ ထည့်သွင်းပေးခြင်း
    if data['start_date'].tzinfo is None:
        data['start_date'] = data['start_date'].replace(tzinfo=timezone.utc)
    if data['end_date'].tzinfo is None:
        data['end_date'] = data['end_date'].replace(tzinfo=timezone.utc)
        
    # ⚡ LOGIC FIX: Start date သည် End date ထက် စောရမည်
    if data['start_date'] >= data['end_date']:
        raise HTTPException(status_code=400, detail="Start date must be before end date.")
        
    try:
        new_period = KPIPeriod(**data)
        db.add(new_period)
        await db.commit()
        await db.refresh(new_period)
        return new_period
    except Exception as e:
        # ⚡ SECURITY & INTEGRITY FIX: Added Rollback
        await db.rollback()
        raise HTTPException(status_code=500, detail="Database error occurred while creating period.")

# ⚡ PERFORMANCE FIX: Added skip and limit
@router.get("/", response_model=List[KPIPeriodResponse])
async def get_kpi_periods(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=100),
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    """Retrieve all active and historical KPI periods"""
    enforce_permission(current_user, "kpi.view")
    stmt = select(KPIPeriod).where(KPIPeriod.is_deleted == False).order_by(KPIPeriod.start_date.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()

@router.put("/{period_id}/status", response_model=KPIPeriodResponse, dependencies=[Depends(admin_only)])
async def update_period_status(
    period_id: UUID,
    new_status: KPIPeriodStatus,
    reason: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Transition a period's state (e.g., DRAFT -> OPEN -> FINALIZED -> LOCKED)"""
    enforce_permission(current_user, "kpi.edit")
    return await period_service.change_period_status(db, period_id, new_status, current_user.id, reason)

# =======================================================================
# 🌟 NEW FEATURE: DELETE DRAFT PERIOD (ယခင် မေးထားသော ဖျက်ခွင့်ပြုရန် API အသစ်)
# =======================================================================
@router.delete("/{period_id}", status_code=status.HTTP_200_OK, dependencies=[Depends(admin_only)])
async def delete_kpi_period(
    period_id: UUID, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a KPI period ONLY if it is in DRAFT status"""
    enforce_permission(current_user, "kpi.edit")
    # 1. ရှာဖွေခြင်း
    stmt = select(KPIPeriod).where(KPIPeriod.id == period_id, KPIPeriod.is_deleted == False)
    result = await db.execute(stmt)
    period = result.scalar_one_or_none()
    
    if not period:
        raise HTTPException(status_code=404, detail="KPI Period not found.")
        
    # 2. 🌟 DRAFT အခြေအနေ ဟုတ်မဟုတ် စစ်ဆေးခြင်း (Safe Delete Guard)
    if period.status != KPIPeriodStatus.DRAFT:
        raise HTTPException(
            status_code=400, 
            detail="Only DRAFT periods can be deleted to protect data integrity. For other periods, use status locking."
        )
        
    # 3. ဖျက်သိမ်းခြင်း (Soft Delete)
    try:
        period.is_deleted = True
        await db.commit()
        return {"success": True, "message": "Draft period deleted successfully."}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Error occurred while deleting period.")
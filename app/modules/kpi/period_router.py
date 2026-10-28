from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.dependencies import enforce_permission, get_current_user
from app.core.timezone import APPLICATION_TIMEZONE
from app.database.connection import get_db
from app.modules.kpi import period_service
from app.modules.kpi.models import KPIPeriod, KPIPeriodStatus
from app.modules.kpi.period_ranges import normalize_calendar_range
from app.modules.kpi.period_schemas import KPIPeriodCreate, KPIPeriodResponse
from app.modules.users.models import User

router = APIRouter(prefix="/periods", tags=["KPI Periods"])

# 🌟 Security: Restrict access to HR and Admins

@router.post(
    "/",
    response_model=KPIPeriodResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Invalid period dates"},
        500: {"description": "Database error"},
    },
)
async def create_kpi_period(payload: KPIPeriodCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Initialize a new KPI evaluation cycle (e.g., Q3 2026 or August 2026)"""
    enforce_permission(current_user, "kpi.create")
    data = payload.model_dump()

    try:
        period_range = normalize_calendar_range(data["start_date"], data["end_date"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data["start_date"] = period_range.start_inclusive
    data["end_date"] = period_range.end_exclusive

    if data["start_date"] >= data["end_date"]:
        raise HTTPException(status_code=400, detail="Start date must be before end date.")
        
    try:
        new_period = KPIPeriod(**data)
        db.add(new_period)
        await db.commit()
        await db.refresh(new_period)
        return new_period
    except Exception:
        # ⚡ SECURITY & INTEGRITY FIX: Added Rollback
        await db.rollback()
        raise HTTPException(status_code=500, detail="Database error occurred while creating period.")

# ⚡ PERFORMANCE FIX: Added skip and limit
@router.get("/", response_model=list[KPIPeriodResponse])
async def get_kpi_periods(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=100),
    year: int | None = Query(None, ge=1900, le=9999),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retrieve all active and historical KPI periods.

    Optional ``year`` filters server-side to periods whose
    ``start_date`` falls in that calendar year (the Periods page's
    year view needs the whole year, which the default newest-50
    window cannot guarantee). Omitting it changes nothing.
    """
    enforce_permission(current_user, "kpi.view")
    stmt = select(KPIPeriod).where(KPIPeriod.is_deleted == False)
    if year is not None:
        # Period boundaries are application-local midnights (see
        # period_ranges), so the year window must be local too — a
        # January period starts at Yangon midnight, which is still
        # December in UTC and would land in the wrong year.
        stmt = stmt.where(
            KPIPeriod.start_date
            >= datetime(year, 1, 1, tzinfo=APPLICATION_TIMEZONE),
            KPIPeriod.start_date
            < datetime(year + 1, 1, 1, tzinfo=APPLICATION_TIMEZONE),
        )
    stmt = stmt.order_by(KPIPeriod.start_date.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()

@router.put(
    "/{period_id}/status",
    response_model=KPIPeriodResponse,
    responses={
        400: {"description": "Invalid period status transition"},
        404: {"description": "KPI period not found"},
    },
)
async def update_period_status(
    period_id: UUID,
    new_status: KPIPeriodStatus,
    reason: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Transition a period's state (e.g., DRAFT -> OPEN -> FINALIZED -> LOCKED)"""
    enforce_permission(current_user, "kpi.edit")
    return await period_service.change_period_status(
        db,
        period_id,
        new_status,
        current_user.id,
        reason,
        current_user.role.name if current_user.role else None,
    )

# =======================================================================
# 🌟 NEW FEATURE: DELETE DRAFT PERIOD (ယခင် မေးထားသော ဖျက်ခွင့်ပြုရန် API အသစ်)
# =======================================================================
@router.delete(
    "/{period_id}",
    status_code=status.HTTP_200_OK,
    responses={
        400: {"description": "Only draft periods can be deleted"},
        404: {"description": "KPI period not found"},
        500: {"description": "Database error"},
    },
)
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
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Error occurred while deleting period.")
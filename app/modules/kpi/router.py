import logging
from typing import List, Optional, Any, Dict
from uuid import UUID
from datetime import datetime, timedelta
import pytz

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc, asc
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import get_db, AsyncSessionLocal
from app.core.dependencies import get_current_user, enforce_permission, enforce_user_scope, enforce_department_scope
from app.core.timezone import APPLICATION_TIMEZONE

from app.modules.users.models import User
from app.modules.departments.models import Department, UserDepartment
from app.models.audit_log import AuditLog  

from app.modules.kpi import service as kpi_service
from app.modules.kpi import repo as kpi_repo 
from app.modules.kpi.models import (
    KPIRecord, PeriodType, KPIFormula, FormulaStatus, KPIPeriodStatus,
    KPILevelSetting, KpiRule
)
from app.modules.kpi.engine import KPICalculationEngine, calculate_custom_period_kpi, formula_max_score

from app.modules.kpi.schemas import (
    KPIFormulaCreate, KPIFormulaUpdate, KPIFormulaResponse,
    KPILevelSettingUpdate, KPILevelSettingResponse, KPIRecordResponse,
    KPIOverrideRequest, KPIDeductionRequest, KPIFreezeRequest,
    KPIRankingResponse, KPIChartResponse, KpiReportSummaryResponse,
    CustomKpiCalculationRequest, KPIChartPoint,
    BulkKpiCalculationRequest
)

# 🌟 1. Setup Production Logger
logger = logging.getLogger(__name__)

router = APIRouter()

# ==============================================================================
# 🌟 2. DEPENDENCIES: Centralized Role-Based Access Control (RBAC)
# ==============================================================================
class RoleChecker:
    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: User = Depends(get_current_user)) -> User:
        if user.role.name not in self.allowed_roles:
            logger.warning(f"Unauthorized access attempt by User {user.id} ({user.role.name})")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Not authorized to perform this action."
            )
        return user

allow_admins = RoleChecker(["Super Admin", "System Super Admin", "Admin"])
allow_super_admins = RoleChecker(["Super Admin", "System Super Admin"])
allow_reports = RoleChecker(["Super Admin", "System Super Admin", "Admin", "HR", "Level 0", "Level 1"])
allow_charts = RoleChecker(["Super Admin", "System Super Admin", "Admin", "Level 0", "Level 1"])


# ==============================================================================
# 🌟 3. SCHEMAS (Locally scoped for API responses)
# ==============================================================================
class DepartmentKPILevelUpdate(BaseModel):
    l0_enabled: bool
    l1_enabled: bool
    l2_enabled: bool

class AuditLogResponse(BaseModel):
    id: str
    timestamp: str
    admin_name: str
    action: str
    table_name: str
    old_value: Dict[str, Any]
    new_value: Dict[str, Any]
    ip_address: str
    
    model_config = ConfigDict(from_attributes=True)


# ==============================================================================
# 1. Admin Setup: L0, L1, L2 KPI Settings
# ==============================================================================
@router.get('/settings', response_model=List[KPILevelSettingResponse])
async def get_kpi_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, "system.settings.view")
    try:
        return await kpi_service.get_kpi_level_settings(db)
    except Exception as e:
        logger.error(f"Failed to fetch KPI settings: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch KPI settings.")

@router.put('/settings/{level_name}', response_model=KPILevelSettingResponse)
async def update_kpi_setting(
    level_name: str,
    payload: KPILevelSettingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, "system.settings.edit")
    await enforce_department_scope(db, current_user, department_id)
    try:
        return await kpi_service.update_kpi_level_setting(db, level_name, payload)
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error updating setting {level_name}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database transaction failed while updating settings.")

@router.put('/settings/department/{department_id}/kpi-levels', tags=["KPI Admin Controls"])
async def update_targeted_department_levels(
    department_id: UUID,
    payload: DepartmentKPILevelUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, "system.settings.edit")
    try:
        levels_map = {
            "L0": payload.l0_enabled,
            "L1": payload.l1_enabled,
            "L2": payload.l2_enabled
        }
        
        stmt = select(KPILevelSetting).where(KPILevelSetting.department_id == department_id)
        existing_settings = (await db.execute(stmt)).scalars().all()
        existing_dict = {s.level_name: s for s in existing_settings}

        for level, is_enabled in levels_map.items():
            if level in existing_dict:
                existing_dict[level].is_enabled = is_enabled
            else:
                db.add(KPILevelSetting(
                    department_id=department_id, 
                    level_name=level, 
                    is_enabled=is_enabled
                ))
                
        await db.commit()
        return {
            "success": True,
            "department_id": str(department_id),
            **levels_map
        }
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error updating targeted department levels for {department_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update department level settings.")


# ==============================================================================
# 2. KPI Formulas
# ==============================================================================
@router.post('/formulas', response_model=KPIFormulaResponse, status_code=status.HTTP_201_CREATED)
async def create_kpi_formula(
    payload: KPIFormulaCreate, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, "kpi.create")
    try:
        return await kpi_service.create_kpi_formula(db, payload)
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Error creating formula: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create KPI formula.")

@router.put('/formulas/{formula_id}', response_model=KPIFormulaResponse)
async def edit_kpi_formula(
    formula_id: UUID,
    payload: KPIFormulaUpdate, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, "kpi.edit")
    try:
        return await kpi_service.update_kpi_formula(db, formula_id, payload)
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Error updating formula {formula_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update KPI formula.")

@router.get('/formulas', response_model=List[KPIFormulaResponse])
async def get_kpi_formulas(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, gt=0, le=500),
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, "kpi.view")
    return await kpi_repo.get_all_formulas(db, skip=skip, limit=limit)

@router.put('/formulas/{formula_id}/toggle', response_model=KPIFormulaResponse)
async def toggle_kpi_formula(
    formula_id: UUID,
    payload: KPIFormulaUpdate,
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, "kpi.edit")
    try:
        formula = await kpi_repo.get_formula_by_id(db, formula_id)
        if not formula:
            raise HTTPException(status_code=404, detail="Formula not found.")
            
        return await kpi_repo.toggle_formula_status(db, formula, payload.is_active)
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Error toggling formula {formula_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to toggle formula status.")

# ------------------------------------------------------------------------------
# Formula State Workflow APIs (With DRY Helper)
# ------------------------------------------------------------------------------
async def _transition_formula_state(db: AsyncSession, formula_id: UUID, current_status: List[FormulaStatus], new_status: FormulaStatus) -> KPIFormula:
    """Helper method to handle repetitive state transitions safely."""
    try:
        formula = await db.get(KPIFormula, formula_id)
        if not formula:
            raise HTTPException(status_code=404, detail="Formula not found.")
            
        if formula.status not in current_status:
            valid_states = ", ".join([s.name for s in current_status])
            raise HTTPException(status_code=400, detail=f"Formula must be in [{valid_states}] to perform this action. Current status is {formula.status.name}.")
            
        formula.status = new_status
        await db.commit()
        await db.refresh(formula)
        return formula
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error transitioning formula {formula_id} to {new_status}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="State transition failed due to a database error.")

@router.post('/formulas/{formula_id}/submit', response_model=KPIFormulaResponse)
async def submit_formula_for_review(formula_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(allow_admins)):
    return await _transition_formula_state(db, formula_id, [FormulaStatus.DRAFT], FormulaStatus.REVIEW)

@router.post('/formulas/{formula_id}/approve', response_model=KPIFormulaResponse)
async def approve_formula(formula_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(allow_super_admins)):
    return await _transition_formula_state(db, formula_id, [FormulaStatus.REVIEW], FormulaStatus.APPROVED)

@router.post('/formulas/{formula_id}/reject', response_model=KPIFormulaResponse)
async def reject_formula(formula_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(allow_super_admins)):
    return await _transition_formula_state(db, formula_id, [FormulaStatus.REVIEW], FormulaStatus.DRAFT)

@router.post('/formulas/{formula_id}/activate', response_model=KPIFormulaResponse)
async def activate_formula(formula_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(allow_admins)):
    return await _transition_formula_state(db, formula_id, [FormulaStatus.APPROVED, FormulaStatus.ARCHIVED], FormulaStatus.ACTIVE)

@router.post('/formulas/{formula_id}/archive', response_model=KPIFormulaResponse)
async def archive_formula(formula_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(allow_admins)):
    return await _transition_formula_state(db, formula_id, [FormulaStatus.ACTIVE, FormulaStatus.APPROVED], FormulaStatus.ARCHIVED)

@router.delete('/formulas/{formula_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_kpi_formula(formula_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(allow_super_admins)):
    try:
        formula = await db.get(KPIFormula, formula_id)
        if not formula:
            raise HTTPException(status_code=404, detail="Formula not found.")
            
        await db.delete(formula)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Error deleting formula {formula_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete formula.")


# ==============================================================================
# 3. KPI Records & Available Categories Matrix Filtering
# ==============================================================================
@router.get('/records/monthly/{target_user_id}', response_model=List[KPIRecordResponse])
async def get_monthly_kpi_records(
    target_user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, "kpi.view")
    await enforce_user_scope(db, current_user, target_user_id)
        
    try:
        return await kpi_service.get_kpi_records_by_user(db, target_user_id, "MONTHLY")
    except Exception as e:
        logger.error(f"Failed to fetch monthly records for {target_user_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve records.")


@router.get('/categories/available', tags=["KPI Categories"])
async def get_available_kpi_categories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, "kpi.view")
    try:
        ud_stmt = select(UserDepartment).where(UserDepartment.user_id == current_user.id)
        ud = (await db.execute(ud_stmt)).scalars().first()
        
        if not ud or not current_user.position_id:
            return {"categories": [], "message": "No organizational context found for your user profile."}
            
        stmt = select(KpiRule.category_name).where(
            KpiRule.department_id == ud.department_id,
            KpiRule.position_id == current_user.position_id,
            KpiRule.level == ud.user_level,
            KpiRule.is_active == True
        )
        categories = (await db.execute(stmt)).scalars().all()
        
        if not categories:
            return {"categories": [], "message": "No KPI categories are currently assigned."}
            
        return {"categories": categories}
    except Exception as e:
        logger.error(f"Error fetching categories for {current_user.id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error retrieving available KPI categories.")


# ==============================================================================
# 4. KPI Admin Controls: Freeze, Override & Deduction
# ==============================================================================
@router.put("/records/{record_id}/freeze", tags=["KPI Admin Controls"])
async def freeze_kpi_record(
    record_id: UUID, 
    payload: KPIFreezeRequest, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, "kpi.edit")
    try:
        record = (await db.execute(
            select(KPIRecord).options(
                selectinload(KPIRecord.period), selectinload(KPIRecord.formula)
            ).where(KPIRecord.id == record_id)
        )).scalars().first()
        if not record:
            raise HTTPException(status_code=404, detail="KPI Record not found.")
        await enforce_user_scope(db, current_user, record.user_id)
        if record.period and record.period.status in [KPIPeriodStatus.FINALIZED, KPIPeriodStatus.LOCKED, KPIPeriodStatus.CANCELLED]:
            raise HTTPException(status_code=409, detail="Finalized KPI records require the correction workflow.")

        record.is_frozen = payload.is_frozen
        await db.commit()
        return {"message": f"KPI Record is now {'Frozen' if payload.is_frozen else 'Unfrozen'}.", "is_frozen": record.is_frozen}
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Failed to freeze record {record_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error while freezing record.")

@router.put("/records/{record_id}/override", response_model=KPIRecordResponse, tags=["KPI Admin Controls"])
async def override_kpi_record(
    record_id: UUID, 
    payload: KPIOverrideRequest, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, "kpi.edit")
    try:
        record = (await db.execute(
            select(KPIRecord).options(
                selectinload(KPIRecord.period), selectinload(KPIRecord.formula)
            ).where(KPIRecord.id == record_id)
        )).scalars().first()
        if not record:
            raise HTTPException(status_code=404, detail="KPI Record not found.")
        await enforce_user_scope(db, current_user, record.user_id)
        if record.is_frozen:
            raise HTTPException(status_code=400, detail="This KPI is frozen and cannot be modified. Unfreeze it first.")

        # Edge case validation
        if payload.new_score < 0:
            raise HTTPException(status_code=400, detail="Override score cannot be negative.")

        old_score = float(record.weighted_score or 0.0)
        old_pct = float(record.percentage or 0.0)

        record.weighted_score = payload.new_score
        max_score = formula_max_score(record.formula) if record.formula else 5.0
        if payload.new_score > max_score:
            raise HTTPException(status_code=400, detail=f"Override score cannot exceed {max_score}.")
        record.percentage = 0.0 if max_score <= 0 else (payload.new_score / max_score) * 100
        record.status_bucket = KPICalculationEngine.get_grade(record.percentage)
        record.is_overridden = True
        record.override_reason = payload.reason

        db.add(AuditLog(
            user_id=current_user.id,
            action="UPDATE_KPI_OVERRIDE",
            table_name="kpi_records",
            record_id=str(record.id),
            old_value={"score": old_score, "percentage": old_pct},
            new_value={"score": float(record.weighted_score), "percentage": float(record.percentage), "reason": payload.reason}
        ))

        await db.commit()
        await db.refresh(record)
        return record
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error overriding KPI {record_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Transaction failed while overriding KPI.")

@router.put("/records/{record_id}/deduct", response_model=KPIRecordResponse, tags=["KPI Admin Controls"])
async def deduct_kpi_record(
    record_id: UUID, 
    payload: KPIDeductionRequest, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, "kpi.edit")
    try:
        record = (await db.execute(
            select(KPIRecord).options(
                selectinload(KPIRecord.period), selectinload(KPIRecord.formula)
            ).where(KPIRecord.id == record_id)
        )).scalars().first()
        if not record:
            raise HTTPException(status_code=404, detail="KPI Record not found.")
        await enforce_user_scope(db, current_user, record.user_id)
        if record.period and record.period.status in [KPIPeriodStatus.FINALIZED, KPIPeriodStatus.LOCKED, KPIPeriodStatus.CANCELLED]:
            raise HTTPException(status_code=409, detail="Finalized KPI records require the correction workflow.")
        if record.is_frozen:
            raise HTTPException(status_code=400, detail="This KPI is frozen and cannot be modified.")

        old_score = float(record.weighted_score or 0.0)
        
        # Explicit edge case handling for logical constraints
        if payload.deduct_points < 0:
            raise HTTPException(status_code=400, detail="Deduction points must be a positive number.")
        if old_score - payload.deduct_points < 0:
            raise HTTPException(status_code=400, detail=f"Cannot deduct {payload.deduct_points} points. Current score is only {old_score}.")

        old_pct = float(record.percentage or 0.0)

        record.weighted_score = max(0.0, record.weighted_score - payload.deduct_points)
        max_score = formula_max_score(record.formula) if record.formula else 5.0
        record.percentage = 0.0 if max_score <= 0 else (record.weighted_score / max_score) * 100
        record.status_bucket = KPICalculationEngine.get_grade(record.percentage)
        record.is_overridden = True
        
        existing_reason = f"{record.override_reason} | " if record.override_reason else ""
        record.override_reason = f"{existing_reason}Deducted {payload.deduct_points} pts: {payload.reason}"

        db.add(AuditLog(
            user_id=current_user.id,
            action="UPDATE_KPI_DEDUCT",
            table_name="kpi_records",
            record_id=str(record.id),
            old_value={"score": old_score, "percentage": old_pct},
            new_value={"score": float(record.weighted_score), "percentage": float(record.percentage), "reason": payload.reason}
        ))

        await db.commit()
        await db.refresh(record)
        return record
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error deducting KPI {record_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Transaction failed while deducting from KPI.")


# ==============================================================================
# 5. Dashboard: Ranking (Leaderboard) - O(1) N+1 Fix
# ==============================================================================
@router.get('/dashboard/ranking', response_model=List[KPIRankingResponse], tags=["Dashboard"])
async def get_kpi_ranking(
    period_type: PeriodType = PeriodType.MONTHLY,
    limit: int = Query(10, gt=0, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, "kpi.view")
    try:
        today = datetime.now(APPLICATION_TIMEZONE).date()
        start_date = today.replace(day=1) if period_type == PeriodType.MONTHLY else today
        
        stmt = select(KPIRecord).options(
            joinedload(KPIRecord.user).selectinload(User.user_departments)
        ).where(
            KPIRecord.period_type == period_type,
            KPIRecord.is_deleted == False,
            KPIRecord.user.has(User.is_deleted == False),
            KPIRecord.period_start >= start_date
        ).order_by(
            desc(KPIRecord.weighted_score), KPIRecord.user_id, KPIRecord.id
        ).limit(limit)
        
        records = (await db.execute(stmt)).scalars().all()
        
        # 🌟 O(1) Bulk fetch departments to eliminate N+1 loop query
        dept_ids = {r.user.user_departments[0].department_id for r in records if r.user.user_departments}
        department_map = {}
        if dept_ids:
            dept_stmt = select(Department.id, Department.name).where(Department.id.in_(dept_ids))
            depts = (await db.execute(dept_stmt)).all()
            department_map = {d.id: d.name for d in depts}

        ranking_list = []
        for index, record in enumerate(records):
            dept_name = "Unknown"
            if record.user.user_departments:
                dept_id = record.user.user_departments[0].department_id
                dept_name = department_map.get(dept_id, "Unknown")

            ranking_list.append(KPIRankingResponse(
                user_id=record.user_id,
                user_name=f"{record.user.first_name} {record.user.last_name}",
                department_name=dept_name,
                total_score=record.weighted_score,
                percentage=record.percentage,
                grade=record.status_bucket.value if hasattr(record.status_bucket, 'value') else str(record.status_bucket),
                rank=index + 1  
            ))
            
        return ranking_list
    except Exception as e:
        logger.error(f"Dashboard ranking generation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate leaderboard.")


# ==============================================================================
# 6. Dashboard: Trend Charts
# ==============================================================================
@router.get('/dashboard/charts/{target_user_id}', response_model=KPIChartResponse, tags=["Dashboard"])
async def get_kpi_trend_chart(
    target_user_id: UUID,
    period_type: PeriodType = PeriodType.MONTHLY,
    days_limit: int = Query(180, le=365), 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, "kpi.view")
    await enforce_user_scope(db, current_user, target_user_id)
 
    try:
        start_date = datetime.now(APPLICATION_TIMEZONE).date() - timedelta(days=days_limit)

        stmt = select(KPIRecord).where(
            KPIRecord.user_id == target_user_id,
            KPIRecord.period_type == period_type,
            KPIRecord.is_deleted == False,
            KPIRecord.period_start >= start_date
        ).order_by(asc(KPIRecord.period_start))
        
        records = (await db.execute(stmt)).scalars().all()
        
        data_points = []
        for record in records:
            date_label = record.period_start.strftime("%Y-%m-%d") if period_type == PeriodType.DAILY else record.period_start.strftime("%b %Y")
            data_points.append(KPIChartPoint(
                date_label=date_label,
                score=record.weighted_score,
                percentage=record.percentage
            ))
            
        return KPIChartResponse(
            user_id=target_user_id,
            period_type=period_type,
            data_points=data_points
        )
    except Exception as e:
        logger.error(f"Dashboard chart generation failed for {target_user_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load trend chart data.")


# ==============================================================================
# 7. KPI Reports: Summary Report
# ==============================================================================
@router.get('/reports/summary', response_model=KpiReportSummaryResponse, tags=["Reports"])
async def get_kpi_summary_report(
    period_type: str = Query(..., description="e.g., DAILY, WEEKLY, MONTHLY"),
    department_id: Optional[UUID] = Query(None, description="Filter by Department"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_reports)
):
    enforce_permission(current_user, "report.view")
    if department_id is not None:
        await enforce_department_scope(db, current_user, department_id)
    try:
        return await kpi_service.KpiReportService.generate_summary_report(
            db=db, 
            period_type=period_type, 
            department_id=department_id
        )
    except Exception as e:
        logger.error(f"Failed to generate summary report: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate report.")


# ==============================================================================
# 8. Custom API: Calculate with exact date
# ==============================================================================
async def run_custom_kpi_background(user_ids: List[UUID], start_date: datetime, end_date: datetime):
    """
    Background worker function that runs calculation non-blocking using a dedicated DB session.
    """
    try:
        async with AsyncSessionLocal() as bg_db:
            for uid in user_ids:
                try:
                    await calculate_custom_period_kpi(
                        db=bg_db, 
                        user_id=uid, 
                        start_date=start_date, 
                        end_date=end_date
                    )
                except Exception as e:
                    # 🌟 FIX: Detailed traceback logging instead of basic print.
                    logger.error(f"[Custom KPI Worker] Calculation failed for User {uid}. Reason: {str(e)}", exc_info=True)
    except Exception as connection_error:
        # Edge condition: What if AsyncSessionLocal fails to connect completely?
        logger.critical(f"[Custom KPI Worker] Fatal Database Connection Error: {str(connection_error)}", exc_info=True)


@router.post('/calculate/custom', tags=["KPI Engine"])
async def trigger_custom_calculation(
    payload: CustomKpiCalculationRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, "kpi.calculate")
    try:
        stmt = select(User.id).where(User.is_active == True)
        user_ids = (await db.execute(stmt)).scalars().all()
        
        if not user_ids:
            raise HTTPException(status_code=400, detail="No active users found to calculate KPIs for.")
            
        # Ensure start date is before end date
        if payload.start_date >= payload.end_date:
            raise HTTPException(status_code=400, detail="Start date must be strictly before end date.")

        background_tasks.add_task(
            run_custom_kpi_background, 
            user_ids, 
            payload.start_date.replace(tzinfo=None), 
            payload.end_date.replace(tzinfo=None)
        )
                
        return {"message": "Custom calculation triggered successfully and is running in the background."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering background KPI calculation: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to initialize background task.")


# ==============================================================================
# 9. Audit Logs Extraction API
# ==============================================================================

@router.post('/calculate', tags=["KPI Engine"])
async def bulk_calculate_endpoint(
    payload: BulkKpiCalculationRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_admins),
):
    """Bulk KPI calculation for multiple users.
    If `user_ids` is empty, all active users are targeted.
    """
    enforce_permission(current_user, "kpi.calculate")
    # Resolve target users
    if payload.user_ids:
        target_user_ids = payload.user_ids
    else:
        # fetch all active user ids
        target_user_ids = await kpi_repo.get_active_user_ids(db)

    if not target_user_ids:
        raise HTTPException(status_code=400, detail="No users found for KPI calculation.")

    # Validate dates
    if payload.start_date >= payload.end_date:
        raise HTTPException(status_code=400, detail="Start date must be before end date.")

    if payload.run_in_background:
        background_tasks.add_task(
            kpi_service.bulk_calculate_kpis,
            db,
            target_user_ids,
            payload.start_date.replace(tzinfo=None),
            payload.end_date.replace(tzinfo=None),
        )
        return {"detail": "Bulk KPI calculation started in background."}

    result = await kpi_service.bulk_calculate_kpis(
        db,
        target_user_ids,
        payload.start_date.replace(tzinfo=None),
        payload.end_date.replace(tzinfo=None),
    )
    return result

@router.get('/audit-logs', response_model=List[AuditLogResponse], tags=["Audit Logs"])
async def get_system_audit_logs(
    limit: int = Query(100, le=1000), 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_super_admins)
):
    try:
        stmt = (
            select(AuditLog, User)
            .outerjoin(User, AuditLog.user_id == User.id)
            .order_by(desc(AuditLog.created_at))
            .limit(limit)
        )

        result = await db.execute(stmt)
        rows = result.all()

        logs = []
        for log, admin in rows:
            admin_name = f"{admin.first_name} {admin.last_name}" if admin else "System Auto"
            
            logs.append(AuditLogResponse(
                id=str(log.id),
                timestamp=f"{log.created_at.isoformat()}Z" if log.created_at else "",
                admin_name=admin_name,
                action=log.action,
                table_name=log.table_name,
                old_value=log.old_value or {},
                new_value=log.new_value or {},
                ip_address=log.ip_address or "Unknown"
            ))

        return logs
    except Exception as e:
        logger.error(f"Error fetching audit logs: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve audit logs.")
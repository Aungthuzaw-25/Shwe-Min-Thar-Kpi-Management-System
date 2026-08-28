from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks # 🌟 FIX: Imported BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc, asc, delete
from sqlalchemy.orm import joinedload, selectinload
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timedelta
import pytz
from app.core.timezone import APPLICATION_TIMEZONE
from pydantic import BaseModel
import json

from app.database.connection import get_db, AsyncSessionLocal # 🌟 FIX: Imported AsyncSessionLocal for Background Worker
from app.core.dependencies import get_current_user
from app.modules.users.models import User
from app.modules.departments.models import Department, UserDepartment
from app.models.audit_log import AuditLog  

from app.modules.kpi import service as kpi_service
from app.modules.kpi import repo as kpi_repo 

from app.modules.kpi.models import KPIRecord, PeriodType, KPIFormula, FormulaStatus, KPILevelSetting, KpiRule, KPIScoringRuleItem
from app.modules.kpi.engine import KPICalculationEngine, calculate_custom_period_kpi, is_level_kpi_enabled

from app.modules.kpi.schemas import (
    KPIFormulaCreate, 
    KPIFormulaUpdate, 
    KPIFormulaResponse,
    KPILevelSettingUpdate,
    KPILevelSettingResponse,
    KPIRecordResponse,
    KPIOverrideRequest,
    KPIDeductionRequest,
    KPIFreezeRequest,
    KPIRankingResponse,
    KPIChartResponse,
    KpiReportSummaryResponse,
    DepartmentLevelSettingRequest,
    CustomKpiCalculationRequest
)

router = APIRouter()

class DepartmentKPILevelUpdate(BaseModel):
    l0_enabled: bool
    l1_enabled: bool
    l2_enabled: bool

# ==============================================================================
# 1. Admin Setup: L0, L1, L2 KPI Settings
# ==============================================================================
@router.get('/settings', response_model=List[KPILevelSettingResponse])
async def get_kpi_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to view settings.")
        
    return await kpi_service.get_kpi_level_settings(db)

@router.put('/settings/{level_name}', response_model=KPILevelSettingResponse)
async def update_kpi_setting(
    level_name: str,
    payload: KPILevelSettingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to modify settings.")
        
    return await kpi_service.update_kpi_level_setting(db, level_name, payload)

@router.put('/settings/department/{department_id}/kpi-levels', tags=["KPI Admin Controls"])
async def update_targeted_department_levels(
    department_id: UUID,
    payload: DepartmentKPILevelUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    levels_map = {
        "L0": payload.l0_enabled,
        "L1": payload.l1_enabled,
        "L2": payload.l2_enabled
    }
    
    for level, is_enabled in levels_map.items():
        stmt = select(KPILevelSetting).where(
            KPILevelSetting.department_id == department_id,
            KPILevelSetting.level_name == level
        )
        setting = (await db.execute(stmt)).scalars().first()
        
        if setting:
            setting.is_enabled = is_enabled
        else:
            new_setting = KPILevelSetting(
                department_id=department_id,
                level_name=level,
                is_enabled=is_enabled
            )
            db.add(new_setting)
            
    await db.commit()
    
    return {
        "success": True,
        "department_id": str(department_id),
        "l0_enabled": payload.l0_enabled,
        "l1_enabled": payload.l1_enabled,
        "l2_enabled": payload.l2_enabled
    }


# ==============================================================================
# 2. 🌟 KPI Formulas (Clean Controllers delegating to service.py)
# ==============================================================================
@router.post('/formulas', response_model=KPIFormulaResponse, status_code=status.HTTP_201_CREATED)
async def create_kpi_formula(
    payload: KPIFormulaCreate, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to define KPI formulas.")
        
    # 🌟 Data Splitting အားလုံးကို service.py မှတစ်ဆင့်သာ အလုပ်လုပ်စေမည်
    return await kpi_service.create_kpi_formula(db, payload)

@router.put('/formulas/{formula_id}', response_model=KPIFormulaResponse)
async def edit_kpi_formula(
    formula_id: UUID,
    payload: KPIFormulaCreate, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to edit KPI formulas.")
        
    # 🌟 Edit လုပ်ရာတွင်လည်း service.py မှသာ Database များကို ထိန်းချုပ်မည်
    return await kpi_service.update_kpi_formula(db, formula_id, payload)

@router.get('/formulas', response_model=List[KPIFormulaResponse])
async def get_kpi_formulas(
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    return await kpi_repo.get_all_formulas(db)

@router.put('/formulas/{formula_id}/toggle', response_model=KPIFormulaResponse)
async def toggle_kpi_formula(
    formula_id: UUID,
    payload: KPIFormulaUpdate,
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to modify KPI formulas.")
        
    formula = await kpi_repo.get_formula_by_id(db, formula_id)
    if not formula:
        raise HTTPException(status_code=404, detail="Formula not found.")
        
    return await kpi_repo.toggle_formula_status(db, formula, payload.is_active)

# ------------------------------------------------------------------------------
# Formula State Workflow APIs 
# ------------------------------------------------------------------------------
@router.post('/formulas/{formula_id}/submit', response_model=KPIFormulaResponse)
async def submit_formula_for_review(
    formula_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
        
    formula = await db.get(KPIFormula, formula_id)
    if not formula:
        raise HTTPException(status_code=404, detail="Formula not found.")
    
    if formula.status != FormulaStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Only DRAFT formulas can be submitted for review.")
        
    formula.status = FormulaStatus.REVIEW
    await db.commit()
    await db.refresh(formula)
    return formula

@router.post('/formulas/{formula_id}/approve', response_model=KPIFormulaResponse)
async def approve_formula(
    formula_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin"]:
        raise HTTPException(status_code=403, detail="Only Super Admins can approve formulas.")
        
    formula = await db.get(KPIFormula, formula_id)
    if not formula:
        raise HTTPException(status_code=404, detail="Formula not found.")
        
    if formula.status != FormulaStatus.REVIEW:
        raise HTTPException(status_code=400, detail="Only IN REVIEW formulas can be approved.")
        
    formula.status = FormulaStatus.APPROVED
    await db.commit()
    await db.refresh(formula)
    return formula

@router.post('/formulas/{formula_id}/reject', response_model=KPIFormulaResponse)
async def reject_formula(
    formula_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin"]:
        raise HTTPException(status_code=403, detail="Only Super Admins can reject formulas.")
        
    formula = await db.get(KPIFormula, formula_id)
    if not formula:
        raise HTTPException(status_code=404, detail="Formula not found.")
        
    if formula.status != FormulaStatus.REVIEW:
        raise HTTPException(status_code=400, detail="Only IN REVIEW formulas can be rejected.")
        
    formula.status = FormulaStatus.DRAFT
    await db.commit()
    await db.refresh(formula)
    return formula

@router.post('/formulas/{formula_id}/activate', response_model=KPIFormulaResponse)
async def activate_formula(
    formula_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
        
    formula = await db.get(KPIFormula, formula_id)
    if not formula:
        raise HTTPException(status_code=404, detail="Formula not found.")
        
    if formula.status not in [FormulaStatus.APPROVED, FormulaStatus.ARCHIVED]:
        raise HTTPException(status_code=400, detail="Only APPROVED or ARCHIVED formulas can be activated.")
        
    formula.status = FormulaStatus.ACTIVE
    await db.commit()
    await db.refresh(formula)
    return formula

@router.post('/formulas/{formula_id}/archive', response_model=KPIFormulaResponse)
async def archive_formula(
    formula_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Not authorized.")
        
    formula = await db.get(KPIFormula, formula_id)
    if not formula:
        raise HTTPException(status_code=404, detail="Formula not found.")
        
    formula.status = FormulaStatus.ARCHIVED
    await db.commit()
    await db.refresh(formula)
    return formula

@router.delete('/formulas/{formula_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_kpi_formula(
    formula_id: UUID,
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to delete.")
        
    formula = await db.get(KPIFormula, formula_id)
    if not formula:
        raise HTTPException(status_code=404, detail="Formula not found.")
        
    await db.delete(formula)
    await db.commit()
    return None


# ==============================================================================
# 3. KPI Records & Available Categories Matrix Filtering
# ==============================================================================
@router.get('/records/monthly/{target_user_id}', response_model=List[KPIRecordResponse])
async def get_monthly_kpi_records(
    target_user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.id != target_user_id and current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to view other user's KPIs.")
        
    return await kpi_service.get_kpi_records_by_user(db, target_user_id, "MONTHLY")


@router.get('/categories/available', tags=["KPI Categories"])
async def get_available_kpi_categories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    ud_stmt = select(UserDepartment).where(UserDepartment.user_id == current_user.id)
    ud = (await db.execute(ud_stmt)).scalars().first()
    
    if not ud or not current_user.position_id:
        return {
            "categories": [], 
            "message": "No organizational context found for your user profile."
        }
        
    stmt = select(KpiRule.category_name).where(
        KpiRule.department_id == ud.department_id,
        KpiRule.position_id == current_user.position_id,
        KpiRule.level == ud.user_level,
        KpiRule.is_active == True
    )
    result = await db.execute(stmt)
    categories = result.scalars().all()
    
    if not categories:
        return {
            "categories": [], 
            "message": "No KPI categories are currently assigned to your Department, Position, and KPI Level."
        }
        
    return {"categories": categories}


# ==============================================================================
# 4. KPI Admin Controls: Freeze, Override & Deduction
# ==============================================================================
@router.put("/records/{record_id}/freeze", tags=["KPI Admin Controls"])
async def freeze_kpi_record(
    record_id: UUID, 
    payload: KPIFreezeRequest, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Only Admins can perform this action.")

    record = await db.get(KPIRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="KPI Record not found.")

    record.is_frozen = payload.is_frozen
    await db.commit()
    
    status_msg = "Frozen" if payload.is_frozen else "Unfrozen"
    return {"message": f"KPI Record is now {status_msg}.", "is_frozen": record.is_frozen}

@router.put("/records/{record_id}/override", response_model=KPIRecordResponse, tags=["KPI Admin Controls"])
async def override_kpi_record(
    record_id: UUID, 
    payload: KPIOverrideRequest, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Only Admins can perform this action.")

    record = await db.get(KPIRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="KPI Record not found.")
        
    if record.is_frozen:
        raise HTTPException(status_code=400, detail="This KPI is frozen and cannot be modified. Unfreeze it first.")

    old_score = record.weighted_score
    old_pct = record.percentage

    record.weighted_score = payload.new_score
    record.percentage = (payload.new_score / 5.0) * 100
    
    record.grade = KPICalculationEngine.get_grade(record.percentage)
    record.status_bucket = record.grade

    record.is_overridden = True
    record.override_reason = payload.reason

    audit_log = AuditLog(
        user_id=current_user.id,
        action="UPDATE_KPI_OVERRIDE",
        table_name="kpi_records",
        record_id=str(record.id),
        old_value={"score": float(old_score) if old_score else 0.0, "percentage": float(old_pct) if old_pct else 0.0},
        new_value={"score": float(record.weighted_score), "percentage": float(record.percentage), "reason": payload.reason}
    )
    db.add(audit_log)

    await db.commit()
    await db.refresh(record)
    return record

@router.put("/records/{record_id}/deduct", response_model=KPIRecordResponse, tags=["KPI Admin Controls"])
async def deduct_kpi_record(
    record_id: UUID, 
    payload: KPIDeductionRequest, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Only Admins can perform this action.")

    record = await db.get(KPIRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="KPI Record not found.")
        
    if record.is_frozen:
        raise HTTPException(status_code=400, detail="This KPI is frozen and cannot be modified.")

    old_score = record.weighted_score
    old_pct = record.percentage

    new_score = record.weighted_score - payload.deduct_points
    record.weighted_score = max(0.0, new_score)
    record.percentage = (record.weighted_score / 5.0) * 100
    
    record.grade = KPICalculationEngine.get_grade(record.percentage)
    record.status_bucket = record.grade

    record.is_overridden = True
    existing_reason = f"{record.override_reason} | " if record.override_reason else ""
    record.override_reason = f"{existing_reason}Deducted {payload.deduct_points} pts: {payload.reason}"

    audit_log = AuditLog(
        user_id=current_user.id,
        action="UPDATE_KPI_DEDUCT",
        table_name="kpi_records",
        record_id=str(record.id),
        old_value={"score": float(old_score) if old_score else 0.0, "percentage": float(old_pct) if old_pct else 0.0},
        new_value={"score": float(record.weighted_score), "percentage": float(record.percentage), "reason": payload.reason}
    )
    db.add(audit_log)

    await db.commit()
    await db.refresh(record)
    return record


# ==============================================================================
# 5. Dashboard: Ranking (Leaderboard) 
# ==============================================================================
@router.get('/dashboard/ranking', response_model=List[KPIRankingResponse], tags=["Dashboard"])
async def get_kpi_ranking(
    period_type: PeriodType = PeriodType.MONTHLY,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    timezone = APPLICATION_TIMEZONE
    today = datetime.now(timezone).date()
    
    start_date = today.replace(day=1) if period_type == PeriodType.MONTHLY else today
    
    stmt = select(KPIRecord).options(
        joinedload(KPIRecord.user).joinedload(User.user_departments)
    ).where(
        KPIRecord.period_type == period_type,
        KPIRecord.period_start >= start_date
    ).order_by(
        desc(KPIRecord.weighted_score)
    ).limit(limit)
    
    records = (await db.execute(stmt)).scalars().all()
    
    ranking_list = []
    for index, record in enumerate(records):
        dept_name = "Unknown"
        if record.user.user_departments:
            dept = await db.get(Department, record.user.user_departments[0].department_id)
            if dept:
                dept_name = dept.name

        ranking_list.append(KPIRankingResponse(
            user_id=record.user_id,
            user_name=f"{record.user.first_name} {record.user.last_name}",
            department_name=dept_name,
            total_score=record.weighted_score,
            percentage=record.percentage,
            grade=record.grade.value if hasattr(record.grade, 'value') else str(record.grade),
            rank=index + 1  
        ))
        
    return ranking_list


# ==============================================================================
# 6. Dashboard: Trend Charts
# ==============================================================================
@router.get('/dashboard/charts/{target_user_id}', response_model=KPIChartResponse, tags=["Dashboard"])
async def get_kpi_trend_chart(
    target_user_id: UUID,
    period_type: PeriodType = PeriodType.MONTHLY,
    days_limit: int = 180, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.id != target_user_id and current_user.role.name not in ["Super Admin", "System Super Admin", "Admin", "Level 0", "Level 1"]:
        raise HTTPException(status_code=403, detail="Not authorized to view other user's charts.")

    timezone = APPLICATION_TIMEZONE
    today = datetime.now(timezone).date()
    start_date = today - timedelta(days=days_limit)

    stmt = select(KPIRecord).where(
        KPIRecord.user_id == target_user_id,
        KPIRecord.period_type == period_type,
        KPIRecord.period_start >= start_date
    ).order_by(asc(KPIRecord.period_start))
    
    records = (await db.execute(stmt)).scalars().all()
    
    data_points = []
    for record in records:
        date_label = record.period_start.strftime("%Y-%m-%d") if period_type == PeriodType.DAILY else record.period_start.strftime("%b %Y")
        
        from app.modules.kpi.schemas import KPIChartPoint
        
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


# ==============================================================================
# 7. KPI Reports: Summary Report
# ==============================================================================
@router.get('/reports/summary', response_model=KpiReportSummaryResponse, tags=["Reports"])
async def get_kpi_summary_report(
    period_type: str = Query(..., description="e.g., DAILY, WEEKLY, MONTHLY"),
    department_id: Optional[UUID] = Query(None, description="Filter by Department"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin", "HR", "Level 0", "Level 1"]:
        raise HTTPException(status_code=403, detail="Not authorized to pull KPI reports.")

    return await kpi_service.KpiReportService.generate_summary_report(
        db=db, 
        period_type=period_type, 
        department_id=department_id
    )


# ==============================================================================
# 8. Custom API: Calculate with exact date (Refactored to Background Task)
# ==============================================================================

# 🌟 BACKGROUND HELPER FOR CUSTOM KPI CALCULATION
async def run_custom_kpi_background(user_ids: List[UUID], start_date: datetime, end_date: datetime):
    """
    Background worker function that runs the calculation in a non-blocking way 
    using a dedicated database session.
    """
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
                # Log individual failures but continue the loop for other users
                print(f"[Custom KPI Background Error for User {uid}]: {str(e)}")


@router.post('/calculate/custom', tags=["KPI Engine"])
async def trigger_custom_calculation(
    payload: CustomKpiCalculationRequest,
    background_tasks: BackgroundTasks, # 🌟 FIX: Injected BackgroundTasks to prevent HTTP 504 Timeout
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin", "Admin"]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    # 🌟 PERFORMANCE FIX: Only pull User IDs instead of full User objects to save memory
    stmt = select(User.id).where(User.is_active == True)
    user_ids = (await db.execute(stmt)).scalars().all()
    
    # 🌟 FIX: Delegate heavy looping to the background thread
    background_tasks.add_task(
        run_custom_kpi_background, 
        user_ids, 
        payload.start_date.replace(tzinfo=None), 
        payload.end_date.replace(tzinfo=None)
    )
            
    return {"message": "Custom calculation triggered successfully and is running in the background."}


# ==============================================================================
# 9. Audit Logs Extraction API
# ==============================================================================
@router.get('/audit-logs', tags=["Audit & Logs"])
async def get_system_audit_logs(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role.name not in ["Super Admin", "System Super Admin"]:
        raise HTTPException(status_code=403, detail="Only Super Admins can view audit logs.")

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
        
        logs.append({
            "id": str(log.id),
            # Database timestamps are UTC-naive; make the contract explicit for clients.
            "timestamp": f"{log.created_at.isoformat()}Z" if log.created_at else "",
            "admin_name": admin_name,
            "action": log.action,
            "table_name": log.table_name,
            "old_value": log.old_value,
            "new_value": log.new_value,
            "ip_address": log.ip_address or "Unknown"
        })

    return logs
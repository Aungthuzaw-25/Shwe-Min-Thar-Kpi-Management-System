import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import String, asc, cast, desc, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload, selectinload

from app.core.dependencies import (
    enforce_department_scope,
    enforce_permission,
    enforce_user_scope,
    get_authorized_department_ids,
    get_current_user,
)
from app.core.timezone import APPLICATION_TIMEZONE
from app.core.idempotency import idempotency_guard
from app.database.connection import AsyncSessionLocal, get_db
from app.models.audit_log import AuditLog
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi import category_service as kpi_category_service
from app.modules.kpi import period_service
from app.modules.kpi import repo as kpi_repo
from app.modules.kpi import service as kpi_service
from app.modules.kpi.engine import (
    KPICalculationContext,
    KPICalculationEngine,
    calculate_custom_period_kpi,
    formula_max_score,
)
from app.modules.kpi.models import (
    FormulaStatus,
    KPIFormula,
    KPIHistory,
    KPILevelSetting,
    KPIPeriodStatus,
    KPIRecord,
    PeriodType,
)
from app.modules.kpi.schemas import (
    BulkKpiCalculationRequest,
    CustomKpiCalculationRequest,
    ExplicitKpiCalculationRequest,
    KPIChartPoint,
    KPIChartResponse,
    KPIDeductionRequest,
    FormulaPeriodsUpdate,
    FormulaReviveRequest,
    KPIFormulaCreate,
    KPIFormulaResponse,
    KPIFormulaUpdate,
    KPIFreezeRequest,
    KPILevelSettingResponse,
    KPILevelSettingUpdate,
    KPIOverrideRequest,
    KPIRankingResponse,
    KPIRecordResponse,
    KpiReportSummaryResponse,
)
from app.modules.users.avatar import avatar_color_index, avatar_initials, avatar_url
from app.modules.users.models import User

# 🌟 1. Setup Production Logger
logger = logging.getLogger(__name__)

router = APIRouter()
ROLE_SUPER_ADMIN = "Super Admin"
ROLE_LEVEL_1 = "Level 1"
ROLE_LEVEL_0 = "Level 0"
ERR_FORMULA_NOT_FOUND = "Formula not found."
PERM_KPI_EDIT = "kpi.edit"
PERM_KPI_VIEW = "kpi.view"
ERR_KPI_RECORD_NOT_FOUND = "KPI Record not found."
PERM_KPI_CALCULATE = "kpi.calculate"

# ==============================================================================
# 🌟 2. DEPENDENCIES: Centralized Role-Based Access Control (RBAC)
# ==============================================================================
class RoleChecker:
    def __init__(self, allowed_roles: list[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: User = Depends(get_current_user)) -> User:
        if user.role.name not in self.allowed_roles:
            logger.warning(f"Unauthorized access attempt by User {user.id} ({user.role.name})")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Not authorized to perform this action."
            )
        return user

allow_admins = RoleChecker([ROLE_SUPER_ADMIN, "Admin"])
allow_super_admins = RoleChecker([ROLE_SUPER_ADMIN])
allow_reports = RoleChecker([ROLE_SUPER_ADMIN, "Admin", "HR", ROLE_LEVEL_0, ROLE_LEVEL_1])
allow_charts = RoleChecker([ROLE_SUPER_ADMIN, "Admin", ROLE_LEVEL_0, ROLE_LEVEL_1])


async def require_report_access(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    """Allow report readers by role or by their assigned department level."""
    allowed_roles = {ROLE_SUPER_ADMIN, "Admin", "HR", ROLE_LEVEL_0, ROLE_LEVEL_1}
    if current_user.role and current_user.role.name in allowed_roles:
        return current_user

    if current_user.role and current_user.role.name == "Department User":
        levels = (
            await db.execute(
                select(UserDepartment.user_level).where(
                    UserDepartment.user_id == current_user.id,
                    UserDepartment.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        normalized_levels = {
            str(level).upper().replace("LEVEL ", "L") for level in levels
        }
        if normalized_levels.intersection({"L0", "L1"}):
            return current_user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authorized to perform this action.",
    )


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
    actor_id: str | None = None
    avatar_url: str | None = None
    initials: str = "?"
    avatar_color_index: int = 0
    action: str
    table_name: str
    old_value: dict[str, Any]
    new_value: dict[str, Any]
    ip_address: str
    
    model_config = ConfigDict(from_attributes=True)


# ==============================================================================
# 1. Admin Setup: L0, L1, L2 KPI Settings
# ==============================================================================
@router.get('/settings', response_model=list[KPILevelSettingResponse], responses={500: {"description": "Internal Server Error"}})
async def get_kpi_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, "system.settings.view")
    try:
        return await kpi_service.get_kpi_level_settings(db)
    except Exception as e:
        logger.exception(f"Failed to fetch KPI settings: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch KPI settings.")

@router.put('/settings/{level_name}', response_model=KPILevelSettingResponse, responses={500: {"description": "Internal Server Error"}})
async def update_kpi_setting(
    level_name: str,
    payload: KPILevelSettingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_super_admins)
):
    enforce_permission(current_user, "system.settings.edit")
    try:
        return await kpi_service.update_kpi_level_setting(db, level_name, payload)
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"Database error updating setting {level_name}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database transaction failed while updating settings.")

@router.put('/settings/department/{department_id}/kpi-levels', tags=["KPI Admin Controls"], responses={500: {"description": "Internal Server Error"}})
async def update_targeted_department_levels(
    department_id: UUID,
    payload: DepartmentKPILevelUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, "system.settings.edit")
    await enforce_department_scope(db, current_user, department_id)
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
        logger.exception(f"Database error updating targeted department levels for {department_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update department level settings.")


async def _load_formula_in_scope(
    db: AsyncSession, current_user: User, formula_id: UUID
) -> KPIFormula:
    formula = await kpi_repo.get_formula_by_id(db, formula_id)
    if not formula or formula.is_deleted:
        raise HTTPException(status_code=404, detail=ERR_FORMULA_NOT_FOUND)
    await enforce_department_scope(db, current_user, formula.department_id)
    return formula


async def _authorized_active_user_ids(
    db: AsyncSession, current_user: User, requested_ids: list[UUID] | None = None
) -> list[UUID]:
    department_ids = await get_authorized_department_ids(db, current_user)
    stmt = select(User.id).where(User.is_active.is_(True), User.is_deleted.is_(False))
    if department_ids is not None:
        database_department_ids = [UUID(department_id) for department_id in department_ids]
        stmt = stmt.where(User.id.in_(select(UserDepartment.user_id).where(
            UserDepartment.department_id.in_(database_department_ids),
            UserDepartment.is_deleted.is_(False),
        )))
    if requested_ids:
        requested = set(requested_ids)
        allowed = set((await db.execute(stmt.where(User.id.in_(requested)))).scalars().all())
        if allowed != requested:
            raise HTTPException(status_code=403, detail="Access denied for this user's organizational scope.")
        return list(allowed)
    return list((await db.execute(stmt)).scalars().all())




async def _formula_response(db: AsyncSession, formula):
    """Attach the lifecycle fields (current period_ids, is_closed,
    and the one-word label closed | linked | unlinked | legacy) to
    the formula object; the response model serializes them."""
    from app.modules.kpi.engine import formula_period_links

    formula_id = getattr(formula, "id", None)
    links = (
        await formula_period_links(db, formula_id)
        if formula_id is not None
        else []
    )
    current = [link.period_id for link in links if link.is_current]
    closed_at = getattr(formula, "closed_at", None)
    formula.period_ids = current
    formula.is_closed = closed_at is not None
    formula.lifecycle_status = (
        "closed" if closed_at is not None
        else "linked" if current
        else "unlinked" if links
        else "legacy"
    )
    return formula


# ==============================================================================
# 2. KPI Formulas
# ==============================================================================
@router.post('/formulas', response_model=KPIFormulaResponse, status_code=status.HTTP_201_CREATED, responses={500: {"description": "Internal Server Error"}})
async def create_kpi_formula(
    payload: KPIFormulaCreate, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, "kpi.create")
    await enforce_department_scope(db, current_user, payload.department_id)
    try:
        created = await kpi_service.create_kpi_formula(db, payload)
        return await _formula_response(db, created)
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"Error creating formula: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create KPI formula.")

@router.put('/formulas/{formula_id}', response_model=KPIFormulaResponse, responses={404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def edit_kpi_formula(
    formula_id: UUID,
    payload: KPIFormulaUpdate, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, PERM_KPI_EDIT)
    await _load_formula_in_scope(db, current_user, formula_id)
    try:
        updated = await kpi_service.update_kpi_formula(db, formula_id, payload)
        return await _formula_response(db, updated)
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"Error updating formula {formula_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update KPI formula.")

@router.get('/formulas', response_model=list[KPIFormulaResponse])
async def get_kpi_formulas(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, gt=0, le=500),
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, PERM_KPI_VIEW)
    formulas = await kpi_repo.get_all_formulas(db, skip=skip, limit=limit)
    department_ids = await get_authorized_department_ids(db, current_user)
    visible = formulas if department_ids is None else [formula for formula in formulas if str(formula.department_id) in department_ids]
    return [await _formula_response(db, formula) for formula in visible]

@router.put('/formulas/{formula_id}/toggle', response_model=KPIFormulaResponse, responses={404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def toggle_kpi_formula(
    formula_id: UUID,
    payload: KPIFormulaUpdate,
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, PERM_KPI_EDIT)
    try:
        formula = await _load_formula_in_scope(db, current_user, formula_id)
        if not formula:
            raise HTTPException(status_code=404, detail=ERR_FORMULA_NOT_FOUND)
            
        toggled = await kpi_repo.toggle_formula_status(db, formula, payload.is_active)
        return await _formula_response(db, toggled)
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"Error toggling formula {formula_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to toggle formula status.")

# ------------------------------------------------------------------------------
# Formula State Workflow APIs (With DRY Helper)
# ------------------------------------------------------------------------------
async def _transition_formula_state(
    db: AsyncSession,
    formula_id: UUID,
    current_status: list[FormulaStatus],
    new_status: FormulaStatus,
    current_user: User,
) -> KPIFormula:
    """Helper method to handle repetitive state transitions safely."""
    try:
        formula = await _load_formula_in_scope(db, current_user, formula_id)
        if not formula:
            raise HTTPException(status_code=404, detail=ERR_FORMULA_NOT_FOUND)
            
        if formula.status not in current_status:
            valid_states = ", ".join([s.name for s in current_status])
            raise HTTPException(status_code=400, detail=f"Formula must be in [{valid_states}] to perform this action. Current status is {formula.status.name}.")
            
        formula.status = new_status
        await db.commit()
        await db.refresh(formula)
        return await _formula_response(db, formula)
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"Database error transitioning formula {formula_id} to {new_status}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="State transition failed due to a database error.")

@router.post('/formulas/{formula_id}/submit', response_model=KPIFormulaResponse, responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def submit_formula_for_review(formula_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(allow_admins)):
    return await _transition_formula_state(db, formula_id, [FormulaStatus.DRAFT], FormulaStatus.REVIEW, _)

@router.post('/formulas/{formula_id}/approve', response_model=KPIFormulaResponse, responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def approve_formula(formula_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(allow_super_admins)):
    return await _transition_formula_state(db, formula_id, [FormulaStatus.REVIEW], FormulaStatus.APPROVED, _)

@router.post('/formulas/{formula_id}/reject', response_model=KPIFormulaResponse, responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def reject_formula(formula_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(allow_super_admins)):
    return await _transition_formula_state(db, formula_id, [FormulaStatus.REVIEW], FormulaStatus.DRAFT, _)

@router.post('/formulas/{formula_id}/activate', response_model=KPIFormulaResponse, responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def activate_formula(formula_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(allow_admins)):
    return await _transition_formula_state(db, formula_id, [FormulaStatus.APPROVED, FormulaStatus.ARCHIVED], FormulaStatus.ACTIVE, _)

@router.post('/formulas/{formula_id}/archive', response_model=KPIFormulaResponse, responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def archive_formula(formula_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(allow_admins)):
    return await _transition_formula_state(db, formula_id, [FormulaStatus.ACTIVE, FormulaStatus.APPROVED], FormulaStatus.ARCHIVED, _)

@router.put('/formulas/{formula_id}/periods', response_model=KPIFormulaResponse, responses={404: {"description": "Not Found"}, 422: {"description": "Unknown period"}, 500: {"description": "Internal Server Error"}})
async def set_formula_periods(
    formula_id: UUID,
    payload: FormulaPeriodsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_admins),
):
    """Set the exact periods this formula applies to (link
    management without the versioning side effects of a full
    formula edit). Links leaving the set become history, never
    deleted rows."""
    enforce_permission(current_user, PERM_KPI_EDIT)
    formula = await _load_formula_in_scope(db, current_user, formula_id)
    if not formula:
        raise HTTPException(status_code=404, detail=ERR_FORMULA_NOT_FOUND)
    await kpi_service.set_formula_period_links(db, formula, payload.period_ids)
    db.add(AuditLog(
        user_id=current_user.id, action="FORMULA_PERIODS_UPDATED",
        table_name="kpi_formulas", record_id=str(formula.id),
        new_value={"period_ids": [str(pid) for pid in payload.period_ids]},
    ))
    await db.commit()
    await db.refresh(formula)
    return await _formula_response(db, formula)


@router.post('/formulas/{formula_id}/close', response_model=KPIFormulaResponse, responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def close_kpi_formula(
    formula_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_admins),
):
    """Manually close a formula (the manual path for legacy
    formulas, which auto-close never touches). A closed formula
    applies nowhere; its rules stay untouched for a later revive."""
    enforce_permission(current_user, PERM_KPI_EDIT)
    formula = await _load_formula_in_scope(db, current_user, formula_id)
    if not formula:
        raise HTTPException(status_code=404, detail=ERR_FORMULA_NOT_FOUND)
    if formula.closed_at is not None:
        raise HTTPException(status_code=400, detail="This formula is already closed.")
    formula.closed_at = datetime.now(UTC)
    await kpi_service.set_formula_period_links(db, formula, [])
    db.add(AuditLog(
        user_id=current_user.id, action="FORMULA_CLOSED",
        table_name="kpi_formulas", record_id=str(formula.id),
        new_value={"manual": True},
    ))
    await db.commit()
    await db.refresh(formula)
    return await _formula_response(db, formula)


@router.post('/formulas/{formula_id}/revive', response_model=KPIFormulaResponse, responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 422: {"description": "Period link required / unknown period"}, 500: {"description": "Internal Server Error"}})
async def revive_kpi_formula(
    formula_id: UUID,
    payload: FormulaReviveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_admins),
):
    """Bring a closed formula back: the SAME record and rules are
    reused — only the closure stamp clears and NEW period links
    are set (at least one is mandatory)."""
    enforce_permission(current_user, PERM_KPI_EDIT)
    formula = await _load_formula_in_scope(db, current_user, formula_id)
    if not formula:
        raise HTTPException(status_code=404, detail=ERR_FORMULA_NOT_FOUND)
    if formula.closed_at is None:
        raise HTTPException(status_code=400, detail="This formula is not closed, so it cannot be revived.")
    if not payload.period_ids:
        raise HTTPException(
            status_code=422,
            detail="Reviving a formula requires linking at least one period. Choose the period(s) it applies to.",
        )
    await kpi_service.set_formula_period_links(db, formula, payload.period_ids)
    formula.closed_at = None
    db.add(AuditLog(
        user_id=current_user.id, action="FORMULA_REVIVED",
        table_name="kpi_formulas", record_id=str(formula.id),
        new_value={"period_ids": [str(pid) for pid in payload.period_ids]},
    ))
    await db.commit()
    await db.refresh(formula)
    return await _formula_response(db, formula)


@router.delete('/formulas/{formula_id}', status_code=status.HTTP_204_NO_CONTENT, responses={404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def delete_kpi_formula(formula_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(allow_super_admins)):
    try:
        formula = await _load_formula_in_scope(db, current_user, formula_id)
        if not formula:
            raise HTTPException(status_code=404, detail=ERR_FORMULA_NOT_FOUND)
            
        await db.delete(formula)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"Error deleting formula {formula_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete formula.")


# ==============================================================================
# 3. KPI Records & Available Categories Matrix Filtering
# ==============================================================================
@router.get('/records/monthly/{target_user_id}', response_model=list[KPIRecordResponse], responses={500: {"description": "Internal Server Error"}})
async def get_monthly_kpi_records(
    target_user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, PERM_KPI_VIEW)
    await enforce_user_scope(db, current_user, target_user_id)
        
    try:
        return await kpi_service.get_kpi_records_by_user(db, target_user_id, "MONTHLY")
    except Exception as e:
        logger.exception(f"Failed to fetch monthly records for {target_user_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve records.")


@router.get('/categories/available', tags=["KPI Categories"])
async def get_available_kpi_categories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, PERM_KPI_VIEW)
    categories = sorted(
        await kpi_category_service.get_eligible_kpi_categories_for_user(
            db, current_user.id
        )
    )
    if not categories:
        return {
            "categories": [],
            "message": "No active KPI categories are configured for your profile.",
        }
    return {"categories": categories}


# ==============================================================================
# 4. KPI Admin Controls: Freeze, Override & Deduction
# ==============================================================================
@router.put("/records/{record_id}/freeze", tags=["KPI Admin Controls"], responses={404: {"description": "Not Found"}, 409: {"description": "Conflict"}, 500: {"description": "Internal Server Error"}})
async def freeze_kpi_record(
    record_id: UUID, 
    payload: KPIFreezeRequest, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, PERM_KPI_EDIT)
    try:
        record = (await db.execute(
            select(KPIRecord).options(
                selectinload(KPIRecord.period), selectinload(KPIRecord.formula)
            ).where(KPIRecord.id == record_id)
        )).scalars().first()
        if not record:
            raise HTTPException(status_code=404, detail=ERR_KPI_RECORD_NOT_FOUND)
        await enforce_user_scope(db, current_user, record.user_id)
        if record.period and record.period.status in [KPIPeriodStatus.FINALIZED, KPIPeriodStatus.LOCKED, KPIPeriodStatus.CANCELLED]:
            raise HTTPException(status_code=409, detail="Finalized KPI records require the correction workflow.")

        record.is_frozen = payload.is_frozen
        await db.commit()
        return {"message": f"KPI Record is now {'Frozen' if payload.is_frozen else 'Unfrozen'}.", "is_frozen": record.is_frozen}
    except SQLAlchemyError as e:
        await db.rollback()
        logger.exception(f"Failed to freeze record {record_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error while freezing record.")

@router.put("/records/{record_id}/override", response_model=KPIRecordResponse, tags=["KPI Admin Controls"], responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def override_kpi_record(
    record_id: UUID, 
    payload: KPIOverrideRequest, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, PERM_KPI_EDIT)
    try:
        record = (await db.execute(
            select(KPIRecord).options(
                selectinload(KPIRecord.period), selectinload(KPIRecord.formula)
            ).where(KPIRecord.id == record_id)
        )).scalars().first()
        if not record:
            raise HTTPException(status_code=404, detail=ERR_KPI_RECORD_NOT_FOUND)
        await enforce_user_scope(db, current_user, record.user_id)
        period_service.ensure_kpi_record_editable(record)
        if record.is_frozen:
            raise HTTPException(status_code=400, detail="This KPI is frozen and cannot be modified. Unfreeze it first.")

        # Edge case validation
        if payload.new_score < 0:
            raise HTTPException(status_code=400, detail="Override score cannot be negative.")

        old_score = float(record.weighted_score or 0.0)
        old_pct = float(record.percentage or 0.0)
        old_status = record.status_bucket

        max_score = formula_max_score(record.formula) if record.formula else 5.0
        if payload.new_score > max_score:
            raise HTTPException(status_code=400, detail=f"Override score cannot exceed {max_score}.")
        record.weighted_score = payload.new_score
        record.percentage = 0.0 if max_score <= 0 else (payload.new_score / max_score) * 100
        record.status_bucket = KPICalculationEngine.get_grade(record.percentage)
        record.is_overridden = True
        record.override_reason = payload.reason

        db.add(KPIHistory(
            kpi_record_id=record.id,
            changed_by_id=current_user.id,
            old_percentage=old_pct,
            new_percentage=float(record.percentage),
            old_weighted_score=old_score,
            new_weighted_score=float(record.weighted_score),
            old_status=old_status,
            new_status=record.status_bucket,
            reason=payload.reason,
        ))
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
        logger.exception(f"Database error overriding KPI {record_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Transaction failed while overriding KPI.")

@router.put("/records/{record_id}/deduct", response_model=KPIRecordResponse, tags=["KPI Admin Controls"], responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def deduct_kpi_record(
    record_id: UUID, 
    payload: KPIDeductionRequest, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, PERM_KPI_EDIT)
    try:
        record = (await db.execute(
            select(KPIRecord).options(
                selectinload(KPIRecord.period), selectinload(KPIRecord.formula)
            ).where(KPIRecord.id == record_id)
        )).scalars().first()
        if not record:
            raise HTTPException(status_code=404, detail=ERR_KPI_RECORD_NOT_FOUND)
        await enforce_user_scope(db, current_user, record.user_id)
        period_service.ensure_kpi_record_editable(record)
        if record.is_frozen:
            raise HTTPException(status_code=400, detail="This KPI is frozen and cannot be modified.")

        old_score = float(record.weighted_score or 0.0)
        old_status = record.status_bucket
        
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

        db.add(KPIHistory(
            kpi_record_id=record.id,
            changed_by_id=current_user.id,
            old_percentage=old_pct,
            new_percentage=float(record.percentage),
            old_weighted_score=old_score,
            new_weighted_score=float(record.weighted_score),
            old_status=old_status,
            new_status=record.status_bucket,
            reason=payload.reason,
        ))
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
        logger.exception(f"Database error deducting KPI {record_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Transaction failed while deducting from KPI.")


# ==============================================================================
# 5. Dashboard: Ranking (Leaderboard) - O(1) N+1 Fix
# ==============================================================================
@router.get('/dashboard/ranking', response_model=list[KPIRankingResponse], tags=["Dashboard"], responses={500: {"description": "Internal Server Error"}})
async def get_kpi_ranking(
    period_type: PeriodType = PeriodType.MONTHLY,
    limit: int = Query(10, gt=0, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Rank from the current month (or today), independent of summary date filters."""
    enforce_permission(current_user, PERM_KPI_VIEW)
    try:
        today = datetime.now(APPLICATION_TIMEZONE).date()
        start_date = today.replace(day=1) if period_type == PeriodType.MONTHLY else today
        authorized_department_ids = await get_authorized_department_ids(db, current_user)
        
        stmt = select(KPIRecord).options(
            joinedload(KPIRecord.user).selectinload(User.user_departments)
        ).where(
            KPIRecord.period_type == period_type,
            KPIRecord.is_deleted == False,
            KPIRecord.user.has(User.is_deleted == False),
            KPIRecord.period_start >= start_date
        )
        if authorized_department_ids is not None:
            stmt = stmt.where(KPIRecord.department_id.in_(authorized_department_ids))

        stmt = stmt.order_by(
            desc(KPIRecord.weighted_score), KPIRecord.user_id, KPIRecord.id
        ).limit(limit)
        
        records = (await db.execute(stmt)).scalars().all()
        
        # 🌟 O(1) Bulk fetch departments to eliminate N+1 loop query
        dept_ids = {r.department_id for r in records if r.department_id is not None}
        department_map = {}
        if dept_ids:
            dept_stmt = select(Department.id, Department.name).where(Department.id.in_(dept_ids))
            depts = (await db.execute(dept_stmt)).all()
            department_map = {d.id: d.name for d in depts}

        ranking_list = []
        for index, record in enumerate(records):
            dept_name = "Unknown"
            if record.department_id is not None:
                dept_name = department_map.get(record.department_id, "Unknown")

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
        logger.exception(f"Dashboard ranking generation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate leaderboard.")


# ==============================================================================
# 6. Dashboard: Trend Charts
# ==============================================================================
@router.get('/dashboard/charts/{target_user_id}', response_model=KPIChartResponse, tags=["Dashboard"], responses={500: {"description": "Internal Server Error"}})
async def get_kpi_trend_chart(
    target_user_id: UUID,
    period_type: PeriodType = PeriodType.MONTHLY,
    days_limit: int = Query(180, le=365), 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Return a rolling ``days_limit`` chart independent of summary date filters."""
    enforce_permission(current_user, PERM_KPI_VIEW)
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
        logger.exception(f"Dashboard chart generation failed for {target_user_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load trend chart data.")


# ==============================================================================
# 7. KPI Reports: Summary Report
# ==============================================================================
def _normalize_report_period_type(period_type: str) -> str:
    try:
        return PeriodType(period_type.strip().upper()).value
    except (AttributeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_type must be one of: DAILY, WEEKLY, MONTHLY.",
        ) from exc


@router.get('/reports/summary', response_model=KpiReportSummaryResponse, tags=["Reports"], responses={422: {"description": "Unprocessable Entity"}, 500: {"description": "Internal Server Error"}})
async def get_kpi_summary_report(
    period_type: str = Query(..., description="e.g., DAILY, WEEKLY, MONTHLY"),
    department_id: UUID | None = Query(None, description="Filter by Department"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_report_access)
):
    enforce_permission(current_user, "report.view")
    normalized_period_type = _normalize_report_period_type(period_type)
    authorized_department_ids = await get_authorized_department_ids(db, current_user)
    if department_id is not None:
        await enforce_department_scope(db, current_user, department_id)
    try:
        return await kpi_service.KpiReportService.generate_summary_report(
            db=db, 
            period_type=normalized_period_type,
            department_id=department_id,
            department_ids=(
                authorized_department_ids if department_id is None else None
            ),
        )
    except Exception as e:
        logger.exception(f"Failed to generate summary report: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate report.")


# ==============================================================================
# 8. Custom API: Calculate with exact date
# ==============================================================================
async def run_custom_kpi_background(user_ids: list[UUID], start_date: datetime, end_date: datetime):
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
                    await bg_db.commit()
                except Exception as e:
                    await bg_db.rollback()
                    # 🌟 FIX: Detailed traceback logging instead of basic print.
                    logger.exception(f"[Custom KPI Worker] Calculation failed for User {uid}. Reason: {str(e)}", exc_info=True)
    except Exception as connection_error:
        # Edge condition: What if AsyncSessionLocal fails to connect completely?
        logger.critical(f"[Custom KPI Worker] Fatal Database Connection Error: {str(connection_error)}", exc_info=True)


@router.post('/calculate/custom', dependencies=[Depends(idempotency_guard)], tags=["KPI Engine"], responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 500: {"description": "Internal Server Error"}})
async def trigger_custom_calculation(
    payload: CustomKpiCalculationRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_admins)
):
    enforce_permission(current_user, PERM_KPI_CALCULATE)
    try:
        user_ids = await _authorized_active_user_ids(db, current_user)
        
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
        logger.exception(f"Error triggering background KPI calculation: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to initialize background task.")


@router.post('/calculate/explicit', dependencies=[Depends(idempotency_guard)], tags=["KPI Engine"], responses={404: {"description": "Not Found"}, 422: {"description": "Unprocessable Entity"}})
async def calculate_kpi_record_explicitly(
    payload: ExplicitKpiCalculationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_admins),
):
    enforce_permission(current_user, PERM_KPI_CALCULATE)
    record = await db.get(KPIRecord, payload.record_id)
    if record is None or record.is_deleted:
        raise HTTPException(status_code=404, detail=ERR_KPI_RECORD_NOT_FOUND)
    await enforce_user_scope(db, current_user, record.user_id)

    context = KPICalculationContext(**payload.model_dump())
    try:
        await KPICalculationEngine.execute_kpi_calculation(db, context=context)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "record_id": str(payload.record_id),
        "detail": "Explicit KPI calculation completed.",
    }


# ==============================================================================
# 9. Audit Logs Extraction API
# ==============================================================================

@router.post('/calculate', dependencies=[Depends(idempotency_guard)], tags=["KPI Engine"], responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}})
async def bulk_calculate_endpoint(
    payload: BulkKpiCalculationRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_admins),
):
    """Bulk KPI calculation for multiple users.
    If `user_ids` is empty, all active users are targeted.
    """
    enforce_permission(current_user, PERM_KPI_CALCULATE)
    # Resolve target users only after validating their server-side department scope.
    target_user_ids = await _authorized_active_user_ids(db, current_user, payload.user_ids)

    if not target_user_ids:
        raise HTTPException(status_code=400, detail="No users found for KPI calculation.")

    # Validate dates
    if payload.start_date >= payload.end_date:
        raise HTTPException(status_code=400, detail="Start date must be before end date.")

    if payload.run_in_background:
        background_tasks.add_task(
            kpi_service.run_bulk_kpi_calculation_background,
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

@router.get('/audit-logs', response_model=list[AuditLogResponse], tags=["Audit Logs"], responses={500: {"description": "Internal Server Error"}})
async def get_system_audit_logs(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None, max_length=200),
    action: str | None = Query(None, max_length=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(allow_super_admins)
):
    try:
        stmt = _audit_log_query(action, search)
        stmt = (
            stmt.order_by(desc(AuditLog.created_at), desc(AuditLog.id))
            .offset(offset)
            .limit(limit)
        )

        result = await db.execute(stmt)
        rows = result.all()

        return [_serialize_audit_log(log, admin) for log, admin in rows]
    except Exception as e:
        logger.exception(f"Error fetching audit logs: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve audit logs.")


def _audit_log_query(action: str | None, search: str | None):
    stmt = select(AuditLog, User).outerjoin(User, AuditLog.user_id == User.id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if search:
        term = f"%{search.strip()}%"
        actor_name = User.first_name + " " + User.last_name
        stmt = stmt.where(
            or_(
                actor_name.ilike(term),
                AuditLog.action.ilike(term),
                AuditLog.table_name.ilike(term),
                AuditLog.ip_address.ilike(term),
                cast(AuditLog.old_value, String).ilike(term),
                cast(AuditLog.new_value, String).ilike(term),
            )
        )
    return stmt


def _serialize_audit_log(log: AuditLog, admin: User | None) -> AuditLogResponse:
    admin_name = f"{admin.first_name} {admin.last_name}" if admin else "System Auto"
    return AuditLogResponse(
        id=str(log.id),
        timestamp=f"{log.created_at.isoformat()}Z" if log.created_at else "",
        admin_name=admin_name,
        actor_id=str(admin.id) if admin else None,
        avatar_url=(
            avatar_url(admin.avatar_path, admin.avatar_updated_at) if admin else None
        ),
        initials=avatar_initials(admin_name, admin.email) if admin else "?",
        avatar_color_index=avatar_color_index(admin.id) if admin else 0,
        action=log.action,
        table_name=log.table_name,
        old_value=log.old_value or {},
        new_value=log.new_value or {},
        ip_address=log.ip_address or "Unknown",
    )
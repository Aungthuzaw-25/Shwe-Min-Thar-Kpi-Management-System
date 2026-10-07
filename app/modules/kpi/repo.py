from typing import Any
from uuid import UUID

from sqlalchemy import func, literal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.modules.departments.models import Department
from app.modules.kpi.models import KPIFormula, KPIRecord
from app.modules.users.models import User


async def create_formula(db: AsyncSession, formula_data: dict) -> KPIFormula:
    try:
        new_formula = KPIFormula(**formula_data)
        db.add(new_formula)
        await db.commit()
        await db.refresh(new_formula)
        return new_formula
    except Exception as e:
        # ⚡ SECURITY & INTEGRITY FIX: Added Rollback
        await db.rollback()
        raise e

# ⚡ PERFORMANCE FIX: Added skip and limit
async def get_all_formulas(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[KPIFormula]:
    stmt = (
        select(KPIFormula)
        .where(KPIFormula.is_deleted == False)
        .order_by(KPIFormula.created_at.desc(), KPIFormula.id)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_formula_by_id(db: AsyncSession, formula_id: UUID) -> KPIFormula | None:
    stmt = select(KPIFormula).where(KPIFormula.id == formula_id)
    result = await db.execute(stmt)
    # ⚡ ACCURACY FIX: scalar_one_or_none
    return result.scalar_one_or_none()

async def toggle_formula_status(db: AsyncSession, formula: KPIFormula, is_active: bool) -> KPIFormula:
    try:
        formula.is_active = is_active
        await db.commit()
        await db.refresh(formula)
        return formula
    except Exception as e:
        await db.rollback()
        raise e

# =====================================================================
# 🌟 KPI Report Data Query (Fixed & Optimized)
# =====================================================================
async def get_kpi_report_data(
    db: AsyncSession, 
    period_type: str, 
    department_id: UUID | None = None,
    skip: int = 0,
    limit: int = 1000, # ⚡ PERFORMANCE FIX: Added Limit
    department_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Report အတွက် KPIRecord, User နှင့် Department Data များကို Join လုပ်၍ ဆွဲထုတ်မည်။
    """
    stmt = (
        select(
            User.id.label("user_id"),
            # ⚡ LOGICAL BUG FIX: Use concat_ws to prevent NULL results if last_name is missing
            func.concat_ws(" ", User.first_name, User.last_name).label("full_name"),
            Department.name.label("department_name"),
            KPIRecord.weighted_score.label("total_score"),
            KPIRecord.status_bucket.label("grade"), 
            literal(1).label("evaluated_tasks_count"), 
            KPIRecord.period_type.label("period_type"),
            KPIRecord.created_at.label("latest_evaluation_date")
        )
        .join(User, KPIRecord.user_id == User.id)
        .outerjoin(Department, KPIRecord.department_id == Department.id)
        .where(
            KPIRecord.period_type == period_type,
            KPIRecord.is_deleted == False,
            User.is_deleted == False,
        )
    )

    if department_id is not None:
        stmt = stmt.where(KPIRecord.department_id == department_id)
    elif department_ids is not None:
        # Empty delegated scope is intentionally an empty result, never global.
        stmt = stmt.where(KPIRecord.department_id.in_(department_ids))

    # ⚡ PERFORMANCE FIX: Pagination applied
    stmt = stmt.order_by(KPIRecord.weighted_score.desc(), KPIRecord.user_id, KPIRecord.id).offset(skip).limit(limit)
    
    result = await db.execute(stmt)
    rows = result.all()
    
    report_data = []
    for row in rows:
        report_data.append({
            "user_id": row.user_id,
            "full_name": row.full_name,
            # Handle null departments safely
            "department_name": row.department_name if row.department_name else "N/A",
            "total_score": float(row.total_score) if row.total_score is not None else 0.0,
            "grade": str(row.grade.value) if hasattr(row.grade, 'value') else str(row.grade),
            "evaluated_tasks_count": int(row.evaluated_tasks_count),
            "period_type": str(row.period_type.value) if hasattr(row.period_type, 'value') else str(row.period_type),
            "latest_evaluation_date": row.latest_evaluation_date
        })
        
    return report_data

async def get_active_user_ids(db: AsyncSession) -> list[UUID]:
    """Return list of UUIDs for users where is_active is True."""
    stmt = select(User.id).where(User.is_active == True)
    result = await db.execute(stmt)
    return [uid for uid in result.scalars().all()]
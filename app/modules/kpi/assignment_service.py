from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from fastapi import HTTPException, status
from uuid import UUID
from datetime import datetime, timezone
from app.modules.kpi.models import KPIAssignment, KPIFormula, KPIPeriod
from app.modules.departments.models import Department, UserDepartment
from app.modules.users.models import Position, User


async def validate_assignment_scope(
    db: AsyncSession,
    department_id: UUID,
    position_id: UUID,
    user_id: UUID,
    formula_id: UUID,
    period_id: UUID,
) -> None:
    department = await db.get(Department, department_id)
    position = await db.get(Position, position_id)
    user = await db.get(User, user_id)
    formula = await db.get(KPIFormula, formula_id)
    period = await db.get(KPIPeriod, period_id)

    if not department or department.is_deleted:
        raise HTTPException(status_code=400, detail="Selected department is not available.")
    if not position or position.department_id != department_id:
        raise HTTPException(status_code=400, detail="Selected position does not belong to the department.")
    if not user or not user.is_active or user.is_deleted:
        raise HTTPException(status_code=400, detail="Selected user is not active.")
    if not formula or not formula.is_active:
        raise HTTPException(status_code=400, detail="Selected KPI formula is not active.")
    if not period or period.is_deleted:
        raise HTTPException(status_code=400, detail="Selected KPI period is not available.")

    membership = await db.execute(
        select(UserDepartment).where(
            UserDepartment.user_id == user_id,
            UserDepartment.department_id == department_id,
        )
    )
    if not membership.scalars().first():
        raise HTTPException(status_code=400, detail="Selected user is not assigned to the department.")
    if user.position_id != position_id:
        raise HTTPException(status_code=400, detail="Selected user is not assigned to the position.")
    if formula.department_id != department_id:
        raise HTTPException(status_code=400, detail="Selected formula does not belong to the department.")

async def check_duplicate_assignment(db: AsyncSession, user_id: UUID, formula_id: UUID, period_id: UUID):
    """ဝန်ထမ်းတစ်ဦးတည်းကို ကာလတစ်ခုတည်းတွင် KPI တစ်ခုတည်း ထပ်မံ Assign လုပ်ခြင်းကို တားဆီးရန်"""
    stmt = select(KPIAssignment).where(
        KPIAssignment.user_id == user_id,
        KPIAssignment.kpi_formula_id == formula_id,
        KPIAssignment.kpi_period_id == period_id,
        KPIAssignment.is_deleted == False
    )
    result = await db.execute(stmt)
    if result.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Duplicate assignment prevented. User {user_id} is already assigned to this KPI for the selected period."
        )

async def assign_kpi_to_user(db: AsyncSession, data: dict, assigner_id: UUID):
    await validate_assignment_scope(
        db,
        data["department_id"],
        data["position_id"],
        data["user_id"],
        data["kpi_formula_id"],
        data["kpi_period_id"],
    )
    await check_duplicate_assignment(db, data['user_id'], data['kpi_formula_id'], data['kpi_period_id'])
    
    assignment = KPIAssignment(
        **data,
        assigned_by_id=assigner_id,
        assigned_at=datetime.now(timezone.utc).replace(tzinfo=None)
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return assignment

async def bulk_assign_kpi(db: AsyncSession, data: dict, assigner_id: UUID):
    assignments = []
    unique_user_ids = set(data["user_ids"])
    if len(unique_user_ids) != len(data["user_ids"]):
        raise HTTPException(status_code=400, detail="Duplicate users are not allowed in one bulk assignment.")

    for user_id in data['user_ids']:
        await validate_assignment_scope(
            db,
            data["department_id"],
            data["position_id"],
            user_id,
            data["kpi_formula_id"],
            data["kpi_period_id"],
        )
        # Transaction မပျက်စေရန် အရင်စစ်ဆေးပါမည်
        await check_duplicate_assignment(db, user_id, data['kpi_formula_id'], data['kpi_period_id'])
        
        assignment = KPIAssignment(
            department_id=data['department_id'],
            position_id=data['position_id'],
            user_id=user_id,
            kpi_formula_id=data['kpi_formula_id'],
            kpi_period_id=data['kpi_period_id'],
            assigned_by_id=assigner_id,
            assigned_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )
        db.add(assignment)
        assignments.append(assignment)
        
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return assignments
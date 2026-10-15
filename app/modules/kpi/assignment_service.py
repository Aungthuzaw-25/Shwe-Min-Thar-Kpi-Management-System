from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.models import KPIAssignment, KPIFormula, KPIPeriod
from app.modules.users.models import Position, User


def _validate_assignment_scope_entities(
    department: Department | None,
    position: Position | None,
    user: User | None,
    formula: KPIFormula | None,
    period: KPIPeriod | None,
    has_membership: bool,
    department_id: UUID,
    position_id: UUID,
) -> None:
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
    if not has_membership:
        raise HTTPException(status_code=400, detail="Selected user is not assigned to the department.")
    if user.position_id != position_id:
        raise HTTPException(status_code=400, detail="Selected user is not assigned to the position.")
    if formula.department_id != department_id:
        raise HTTPException(status_code=400, detail="Selected formula does not belong to the department.")


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
            UserDepartment.is_deleted.is_(False),
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
    )
    result = await db.execute(stmt)
    assignment = result.scalars().first()
    if assignment and not assignment.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Duplicate assignment prevented. User {user_id} is already assigned to this KPI for the selected period."
        )
    return assignment


def _assignment_values(data: dict, assigner_id: UUID) -> dict:
    return {
        "department_id": data["department_id"],
        "position_id": data["position_id"],
        "user_id": data["user_id"],
        "kpi_formula_id": data["kpi_formula_id"],
        "kpi_period_id": data["kpi_period_id"],
        "assigned_by_id": assigner_id,
        "assigned_at": datetime.now(UTC).replace(tzinfo=None),
    }


def _restore_assignment(assignment: KPIAssignment, data: dict, assigner_id: UUID) -> KPIAssignment:
    for field, value in _assignment_values(data, assigner_id).items():
        setattr(assignment, field, value)
    assignment.is_deleted = False
    return assignment

async def assign_kpi_to_user(db: AsyncSession, data: dict, assigner_id: UUID):
    await validate_assignment_scope(
        db,
        data["department_id"],
        data["position_id"],
        data["user_id"],
        data["kpi_formula_id"],
        data["kpi_period_id"],
    )
    existing = await check_duplicate_assignment(
        db, data["user_id"], data["kpi_formula_id"], data["kpi_period_id"]
    )
    if existing:
        assignment = _restore_assignment(existing, data, assigner_id)
    else:
        assignment = KPIAssignment(**_assignment_values(data, assigner_id))
        db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return assignment

async def bulk_assign_kpi(db: AsyncSession, data: dict, assigner_id: UUID):
    unique_user_ids = set(data["user_ids"])
    if len(unique_user_ids) != len(data["user_ids"]):
        raise HTTPException(status_code=400, detail="Duplicate users are not allowed in one bulk assignment.")

    if not unique_user_ids:
        await db.commit()
        return []

    department = await db.get(Department, data["department_id"])
    position = await db.get(Position, data["position_id"])
    formula = await db.get(KPIFormula, data["kpi_formula_id"])
    period = await db.get(KPIPeriod, data["kpi_period_id"])
    users_result = await db.execute(select(User).where(User.id.in_(unique_user_ids)))
    users = {user.id: user for user in users_result.scalars().all()}
    memberships_result = await db.execute(
        select(UserDepartment.user_id).where(
            UserDepartment.user_id.in_(unique_user_ids),
            UserDepartment.department_id == data["department_id"],
            UserDepartment.is_deleted.is_(False),
        )
    )
    member_user_ids = set(memberships_result.scalars().all())
    existing_result = await db.execute(
        select(KPIAssignment).where(
            KPIAssignment.user_id.in_(unique_user_ids),
            KPIAssignment.kpi_formula_id == data["kpi_formula_id"],
            KPIAssignment.kpi_period_id == data["kpi_period_id"],
        )
    )
    existing_assignments = {
        assignment.user_id: assignment for assignment in existing_result.scalars().all()
    }

    pending_assignments = []
    pending_restores = []
    assignments = []
    for user_id in data['user_ids']:
        _validate_assignment_scope_entities(
            department,
            position,
            users.get(user_id),
            formula,
            period,
            user_id in member_user_ids,
            data["department_id"],
            data["position_id"],
        )
        existing = existing_assignments.get(user_id)
        if existing and not existing.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate assignment prevented. User {user_id} is already assigned to this KPI for the selected period."
            )
        if existing:
            assignment = existing
            pending_restores.append((assignment, {
                "department_id": data["department_id"],
                "position_id": data["position_id"],
                "user_id": user_id,
                "kpi_formula_id": data["kpi_formula_id"],
                "kpi_period_id": data["kpi_period_id"],
            }))
        else:
            assignment = KPIAssignment(
                **_assignment_values(
                    {
                        "department_id": data["department_id"],
                        "position_id": data["position_id"],
                        "user_id": user_id,
                        "kpi_formula_id": data["kpi_formula_id"],
                        "kpi_period_id": data["kpi_period_id"],
                    },
                    assigner_id,
                )
            )
            pending_assignments.append(assignment)
        assignments.append(assignment)

    for assignment, assignment_data in pending_restores:
        _restore_assignment(assignment, assignment_data, assigner_id)
    for assignment in pending_assignments:
        db.add(assignment)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return assignments
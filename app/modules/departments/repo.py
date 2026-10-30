from uuid import UUID

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.modules.departments.models import Department, UserDepartment
from app.modules.users.models import User


def member_count_expr():
    """Correlated count of a department's CURRENT memberships.

    Counts UserDepartment rows that are not soft-deleted AND whose
    user is not soft-deleted — exactly what the department
    workspace members list shows (it filters User.is_deleted;
    no code path sets UserDepartment.is_deleted, but the row
    filter is kept so a future soft-delete path is honoured).
    The head counts exactly when the head holds a live row
    (the normal case: an L0 assignment creates the row); a
    bare head_id with no row adds nothing — the count is
    row-based, not head-based. Deactivated-but-not-deleted
    users still count (the members view filters is_deleted
    only, not is_active).
    """
    return (
        select(func.count())
        .select_from(UserDepartment)
        .join(User, UserDepartment.user_id == User.id)
        .where(
            UserDepartment.department_id == Department.id,
            UserDepartment.is_deleted == False,
            User.is_deleted == False,
        )
        .correlate(Department)
        .scalar_subquery()
    )


async def count_department_members(db: AsyncSession, dept_id: UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(UserDepartment)
        .join(User, UserDepartment.user_id == User.id)
        .where(
            UserDepartment.department_id == dept_id,
            UserDepartment.is_deleted == False,
            User.is_deleted == False,
        )
    )
    result = await db.execute(stmt)
    return result.scalar() or 0


async def list_departments_with_member_counts(
    db: AsyncSession,
    *,
    is_deleted: bool,
    sort_desc: bool = True,
    skip: int = 0,
    limit: int = 100,
    scope_user_id: UUID | None = None,
) -> list[Department]:
    """Departments ordered SERVER-SIDE by live member count.

    Ordering by the count expression (not a stored rank) means
    skip/limit pages over the true order and a department that
    gains a member moves up on the very next list call.
    Tiebreakers: name asc, then id (names are unique, so the
    id tiebreak only guarantees determinism). The scope filter
    (non-SA/HR see only their own departments) is applied
    before ordering, as before.
    """
    count = member_count_expr()
    order = count.desc() if sort_desc else count.asc()
    stmt = (
        select(Department, count.label("member_count"))
        .where(Department.is_deleted == is_deleted)
        .order_by(order, Department.name, Department.id)
        .offset(skip)
        .limit(limit)
    )
    if scope_user_id is not None:
        stmt = stmt.where(
            Department.id.in_(
                select(UserDepartment.department_id).where(
                    UserDepartment.user_id == scope_user_id
                )
            )
        )
    result = await db.execute(stmt)
    departments = []
    for dept, member_count in result.all():
        dept.member_count = member_count
        departments.append(dept)
    return departments


async def create_department(db: AsyncSession, dept_data: dict) -> Department:
    try:
        new_dept = Department(**dept_data)
        db.add(new_dept)
        await db.commit()
        await db.refresh(new_dept)
        return new_dept
    except Exception as e:
        await db.rollback()
        raise e

async def get_all_departments(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[Department]:
    return await list_departments_with_member_counts(
        db, is_deleted=False, sort_desc=True, skip=skip, limit=limit
    )

async def get_department_by_id(db: AsyncSession, dept_id: UUID) -> Department | None:
    stmt = select(Department).where(Department.id == dept_id, Department.is_deleted == False)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def get_department_with_positions(db: AsyncSession, dept_id: UUID) -> Department | None:
    stmt = select(Department).options(selectinload(Department.positions)).where(Department.id == dept_id, Department.is_deleted == False)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def get_department_by_name(db: AsyncSession, name: str) -> Department | None:
    stmt = select(Department).where(Department.name == name, Department.is_deleted == False)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def get_department_any(db: AsyncSession, dept_id: UUID) -> Department | None:
    stmt = select(Department).where(Department.id == dept_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def check_users_in_dept(db: AsyncSession, dept_id: UUID) -> bool:
    # 🌟 CRITICAL BUG FIX (DBA): Changed from User to UserDepartment pivot table
    stmt = select(func.count()).select_from(UserDepartment).where(UserDepartment.department_id == dept_id)
    result = await db.execute(stmt)
    count = result.scalar()
    return count > 0

async def update_department(db: AsyncSession, dept: Department, update_data: dict) -> Department:
    try:
        for key, value in update_data.items():
            setattr(dept, key, value)
        await db.commit()
        await db.refresh(dept)
        return dept
    except Exception as e:
        await db.rollback()
        raise e

async def delete_department(db: AsyncSession, dept: Department) -> Department:
    try:
        dept.is_deleted = True
        await db.commit()
        return dept
    except Exception as e:
        await db.rollback()
        raise e

async def restore_department(db: AsyncSession, dept: Department) -> Department:
    try:
        dept.is_deleted = False
        await db.commit()
        await db.refresh(dept)
        return dept
    except Exception as e:
        await db.rollback()
        raise e

async def hard_delete_department(db: AsyncSession, dept: Department):
    try:
        await db.delete(dept)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise e
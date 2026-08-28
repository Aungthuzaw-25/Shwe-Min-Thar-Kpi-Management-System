from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from sqlalchemy.orm import selectinload 
from uuid import UUID
from typing import List, Optional

from app.modules.departments.models import Department, UserDepartment
from app.modules.users.models import User

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

async def get_all_departments(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[Department]:
    stmt = select(Department).where(Department.is_deleted == False).order_by(Department.name).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_department_by_id(db: AsyncSession, dept_id: UUID) -> Optional[Department]:
    stmt = select(Department).where(Department.id == dept_id, Department.is_deleted == False)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def get_department_with_positions(db: AsyncSession, dept_id: UUID) -> Optional[Department]:
    stmt = select(Department).options(selectinload(Department.positions)).where(Department.id == dept_id, Department.is_deleted == False)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def get_department_by_name(db: AsyncSession, name: str) -> Optional[Department]:
    stmt = select(Department).where(Department.name == name, Department.is_deleted == False)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def get_department_any(db: AsyncSession, dept_id: UUID) -> Optional[Department]:
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
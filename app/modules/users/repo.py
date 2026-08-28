from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from app.modules.users.models import User
from app.modules.departments.models import UserDepartment
import uuid

# 🌟 Reusable Base Query for User with standard Eager Load relations
def get_user_base_query():
    return select(User).options(
        selectinload(User.role),
        selectinload(User.position), 
        selectinload(User.user_departments).selectinload(UserDepartment.department)
    )

async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    stmt = get_user_base_query().where(User.email == email, User.is_deleted == False)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    stmt = get_user_base_query().where(User.id == user_id, User.is_deleted == False)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def create_user(db: AsyncSession, user_data: dict) -> User:
    new_user = User(**user_data)
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    # Reload with relations so position and departments are fully loaded in return object
    return await get_user_by_id(db, new_user.id)

# ⚡ PERFORMANCE FIX: Added skip and limit for Pagination
async def get_all_users(db: AsyncSession, skip: int = 0, limit: int = 100):
    """
    User အားလုံးကို ဆွဲထုတ်မည့်အစား limit သတ်မှတ်၍ ဆွဲထုတ်မည်။
    (Default အနေဖြင့် တစ်ခါခေါ်လျှင် အယောက် ၁၀၀ သာ ဆွဲထုတ်မည်)
    """
    stmt = get_user_base_query().where(User.is_deleted == False).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()

# (Optionally) Deleted Users တွေကိုပါ Pagination လုပ်ထားသင့်ပါတယ်
async def get_deleted_users(db: AsyncSession, skip: int = 0, limit: int = 100):
    stmt = get_user_base_query().where(User.is_deleted == True).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()

async def update_user(db: AsyncSession, user: User, update_data: dict) -> User:
    for key, value in update_data.items():
        setattr(user, key, value)
    await db.commit()
    await db.refresh(user)
    return await get_user_by_id(db, user.id)

async def soft_delete_user(db: AsyncSession, user: User) -> User:
    user.is_deleted = True
    user.is_active = False  # လုံခြုံရေးအရ Account ကိုပါ ပိတ်ပါမည်
    await db.commit()
    return user
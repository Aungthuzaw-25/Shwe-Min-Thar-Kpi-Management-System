from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from uuid import UUID
from typing import List
from app.modules.users.models import Permission, RolePermission, Role

async def get_all_permissions(db: AsyncSession) -> List[Permission]:
    stmt = select(Permission).order_by(Permission.code)
    result = await db.execute(stmt)
    return result.scalars().all()

async def get_role_permissions(db: AsyncSession, role_id: UUID) -> List[Permission]:
    stmt = select(Permission).join(RolePermission).where(RolePermission.role_id == role_id)
    result = await db.execute(stmt)
    return result.scalars().all()

async def assign_permissions_to_role(db: AsyncSession, role_id: UUID, permission_codes: List[str]):
    # 1. ယခင်ရှိပြီးသား Permission များကို အရင်ဖျက်ပါမည် (Clean Slate)
    stmt_delete = select(RolePermission).where(RolePermission.role_id == role_id)
    result = await db.execute(stmt_delete)
    old_permissions = result.scalars().all()
    for old_perm in old_permissions:
        await db.delete(old_perm)

    # 2. တောင်းဆိုထားသော Permission Code အသစ်များ၏ ID ကို ရှာမည်
    if permission_codes:
        stmt_perms = select(Permission).where(Permission.code.in_(permission_codes))
        result_perms = await db.execute(stmt_perms)
        valid_permissions = result_perms.scalars().all()

        # 3. RolePermission Pivot Table သို့ အသစ်ပြန်ထည့်မည်
        for perm in valid_permissions:
            new_role_perm = RolePermission(role_id=role_id, permission_id=perm.id)
            db.add(new_role_perm)

    await db.commit()
    return await get_role_permissions(db, role_id)
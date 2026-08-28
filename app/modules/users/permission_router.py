from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from uuid import UUID

from app.database.connection import get_db
from app.core.dependencies import RoleChecker
from app.modules.users import permission_repo
from app.modules.users.permission_schemas import PermissionResponse, AssignPermissionsRequest
from app.modules.users.models import User

router = APIRouter(prefix="/permissions", tags=["Permissions"])

# 🌟 Super Admin နှင့် Admin များသာ Permission များကို စီမံခွင့်ရှိသည်
admin_only = RoleChecker(["System Super Admin", "Super Admin", "Admin"])

@router.get("/", response_model=List[PermissionResponse], dependencies=[Depends(admin_only)])
async def list_all_permissions(db: AsyncSession = Depends(get_db)):
    """System အတွင်းရှိ Granular Permission အားလုံးကို ဆွဲထုတ်ရန်"""
    return await permission_repo.get_all_permissions(db)

@router.get("/role/{role_id}", response_model=List[PermissionResponse], dependencies=[Depends(admin_only)])
async def get_permissions_for_role(role_id: UUID, db: AsyncSession = Depends(get_db)):
    """သတ်မှတ်ထားသော Role တစ်ခုတွင် ရရှိထားသည့် Permission များကို ကြည့်ရန်"""
    return await permission_repo.get_role_permissions(db, role_id)

@router.put("/role/{role_id}", response_model=List[PermissionResponse], dependencies=[Depends(admin_only)])
async def update_role_permissions(
    role_id: UUID, 
    payload: AssignPermissionsRequest, 
    db: AsyncSession = Depends(get_db)
):
    """Role တစ်ခု၏ Permission များကို အသစ်ပြင်ဆင် သတ်မှတ်ပေးရန်"""
    return await permission_repo.assign_permissions_to_role(db, role_id, payload.permission_codes)
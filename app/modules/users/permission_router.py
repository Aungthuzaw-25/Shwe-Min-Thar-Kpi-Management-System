from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc, select
from typing import List
from uuid import UUID

from app.database.connection import get_db
from app.core.dependencies import RequirePermission, get_permission_map
from app.modules.users import permission_repo
from app.modules.users.permission_schemas import PermissionResponse, AssignPermissionsRequest, UserPermissionOverrideRequest, UserPermissionOverrideResponse
from app.modules.users.models import User, Role, Permission, UserPermissionOverride
from app.modules.departments.models import Department
from app.utils.audit import log_audit_trail
from app.models.audit_log import AuditLog

router = APIRouter(prefix="/permissions", tags=["Permissions"])

# Permission management itself is permission-protected.  Super Admin is
# handled as a bypass by RequirePermission; other roles may be delegated it.
admin_only = RequirePermission(["system.permissions.manage"])

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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_only),
):
    """Role တစ်ခု၏ Permission များကို အသစ်ပြင်ဆင် သတ်မှတ်ပေးရန်"""
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found.")
    before = [p.code for p in role.permissions]
    result = await permission_repo.assign_permissions_to_role(db, role_id, payload.permission_codes)
    await log_audit_trail(db, current_user.id, "UPDATE_ROLE_PERMISSIONS", "roles", str(role_id),
                          {"permissions": before}, {"permissions": payload.permission_codes})
    return result

@router.get("/users", response_model=List[dict], dependencies=[Depends(admin_only)])
async def list_permission_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.is_deleted == False).order_by(User.first_name, User.last_name))
    return [{"id": str(u.id), "name": f"{u.first_name} {u.last_name}", "email": u.email,
             "role_id": str(u.role_id)} for u in result.scalars().all()]

@router.get("/user/{user_id}", response_model=List[UserPermissionOverrideResponse], dependencies=[Depends(admin_only)])
async def get_user_overrides(user_id: UUID, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(UserPermissionOverride, Permission)
        .join(Permission, Permission.id == UserPermissionOverride.permission_id)
        .where(UserPermissionOverride.user_id == user_id)
    )).all()
    return [{"permission_code": p.code, "description": p.description, "allowed": o.allowed,
             "scope": o.scope, "department_id": o.department_id} for o, p in rows]

@router.put("/user/{user_id}", response_model=List[UserPermissionOverrideResponse])
async def set_user_override(
    user_id: UUID,
    payload: UserPermissionOverrideRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_only),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=403, detail="You cannot change your own permissions.")
    target = (await db.execute(select(User).where(User.id == user_id, User.is_deleted == False))).scalar_one_or_none()
    permission = (await db.execute(select(Permission).where(Permission.code == payload.permission_code))).scalar_one_or_none()
    if not target or not permission:
        raise HTTPException(status_code=404, detail="User or permission not found.")
    if payload.scope not in {"OWN", "DEPARTMENT", "ALL"}:
        raise HTTPException(status_code=422, detail="Scope must be OWN, DEPARTMENT, or ALL.")
    if payload.scope == "DEPARTMENT" and not payload.department_id:
        raise HTTPException(status_code=422, detail="Department is required for department scope.")
    if payload.scope != "DEPARTMENT" and payload.department_id:
        raise HTTPException(status_code=422, detail="Department is only valid with DEPARTMENT scope.")
    if payload.department_id:
        department = await db.get(Department, payload.department_id)
        if not department or department.is_deleted:
            raise HTTPException(status_code=404, detail="Department not found.")
    existing = (await db.execute(select(UserPermissionOverride).where(
        UserPermissionOverride.user_id == user_id,
        UserPermissionOverride.permission_id == permission.id,
        UserPermissionOverride.department_id == payload.department_id
    ))).scalar_one_or_none()
    old = {"allowed": existing.allowed, "scope": existing.scope} if existing else None
    if existing:
        existing.allowed, existing.scope = payload.allowed, payload.scope
    else:
        db.add(UserPermissionOverride(user_id=user_id, permission_id=permission.id, allowed=payload.allowed,
                                      scope=payload.scope, department_id=payload.department_id))
    await db.commit()
    await log_audit_trail(db, current_user.id, "UPDATE_USER_PERMISSION", "users", str(user_id),
                          old, {"permission_code": payload.permission_code, "allowed": payload.allowed, "scope": payload.scope})
    return await get_user_overrides(user_id, db)

@router.delete("/user/{user_id}/{permission_code}", status_code=204)
async def delete_user_override(
    user_id: UUID,
    permission_code: str,
    department_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
                               current_user: User = Depends(admin_only)):
    if user_id == current_user.id:
        raise HTTPException(status_code=403, detail="You cannot change your own permissions.")
    permission = (await db.execute(select(Permission).where(Permission.code == permission_code))).scalar_one_or_none()
    override = (await db.execute(select(UserPermissionOverride).where(
        UserPermissionOverride.user_id == user_id,
        UserPermissionOverride.permission_id == permission.id,
        UserPermissionOverride.department_id == department_id
    ))).scalars().first() if permission else None
    if override:
        old = {
            "permission_code": permission_code,
            "allowed": override.allowed,
            "scope": override.scope,
            "department_id": str(override.department_id) if override.department_id else None,
        }
        await db.delete(override)
        await db.commit()
        await log_audit_trail(db, current_user.id, "DELETE_USER_PERMISSION", "users", str(user_id),
                              old, None)

@router.get("/audit", response_model=List[dict], dependencies=[Depends(admin_only)])
async def permission_audit_history(limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AuditLog).where(AuditLog.action.in_([
            "UPDATE_ROLE_PERMISSIONS", "UPDATE_USER_PERMISSION", "DELETE_USER_PERMISSION"
        ])).order_by(desc(AuditLog.created_at)).limit(min(limit, 500))
    )
    return [{
        "id": str(log.id), "action": log.action, "record_id": log.record_id,
        "old_value": log.old_value, "new_value": log.new_value,
        "actor_id": str(log.user_id) if log.user_id else None,
        "created_at": f"{log.created_at.isoformat()}Z" if log.created_at else None
    } for log in result.scalars().all()]


@router.get("/me", response_model=dict)
async def my_permission_map(current_user: User = Depends(__import__(
    "app.core.dependencies", fromlist=["get_current_user"]
).get_current_user)):
    """Expose the current user's effective global permissions to the UI."""
    return {
        "user_id": str(current_user.id),
        "role": current_user.role.name if current_user.role else None,
        "permissions": get_permission_map(current_user),
    }


@router.get("/departments", response_model=List[dict], dependencies=[Depends(admin_only)])
async def permission_departments(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Department).where(Department.is_deleted == False).order_by(Department.name)
    )).scalars().all()
    return [{"id": str(row.id), "name": row.name} for row in rows]
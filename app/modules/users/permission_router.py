from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import RequirePermission, get_current_user, get_permission_map
from app.core.permission_versions import (
    bump_role_permissions_version,
    bump_user_permissions_version,
    permissions_version_for,
)
from app.core.exceptions import PermissionDeniedException
from app.database.connection import get_db
from app.models.audit_log import AuditLog
from app.modules.departments.models import Department, UserDepartment
from app.modules.users import permission_repo
from app.modules.users.avatar import avatar_color_index, avatar_initials, avatar_url
from app.modules.users.models import Permission, Role, User, UserPermissionOverride
from app.modules.users.permission_schemas import (
    AssignPermissionsRequest,
    BulkUserOverrideRequest,
    PermissionResponse,
    UserPermissionOverrideRequest,
    UserPermissionOverrideResponse,
)
from app.utils.audit import log_audit_trail

router = APIRouter(prefix="/permissions", tags=["Permissions"])

# Permission management itself is permission-protected.  Super Admin is
# handled as a bypass by RequirePermission; other roles may be delegated it.
admin_only = RequirePermission(["system.permissions.manage"])

SUPER_ADMIN_ROLE = "Super Admin"


# =====================================================================
# Helper: resolve whether the current admin can manage a target user
# =====================================================================
async def _assert_can_manage_target(
    db: AsyncSession, current_user: User, target_user_id: UUID
):
    """Non-Super-Admin admins may only manage users in their own departments."""
    if current_user.role and current_user.role.name == SUPER_ADMIN_ROLE:
        return  # Super Admin can manage everyone

    if target_user_id == current_user.id:
        raise HTTPException(status_code=403, detail="You cannot change your own permissions.")

    # Fetch admin's departments
    admin_depts = (await db.execute(
        select(UserDepartment.department_id).where(
            UserDepartment.user_id == current_user.id,
            UserDepartment.is_deleted == False,
        )
    )).scalars().all()

    # Fetch target's departments
    target_depts = (await db.execute(
        select(UserDepartment.department_id).where(
            UserDepartment.user_id == target_user_id,
            UserDepartment.is_deleted == False,
        )
    )).scalars().all()

    if not set(target_depts) & set(admin_depts):
        raise HTTPException(
            status_code=403,
            detail="You can only manage users within your own department.",
        )


async def _assert_can_read_target(
    db: AsyncSession, current_user: User, target_user_id: UUID
):
    """E4 fix (roles audit): the two permission READ endpoints carried
    only admin_only, so any permission-manager could read ANY user's
    overrides/effective matrix. Reads now use the same department
    scope as mutations — with one deliberate difference: reading
    your OWN matrix is allowed (mutations forbid it, reads must
    not). Super Admins read everyone."""
    if current_user.role and current_user.role.name == SUPER_ADMIN_ROLE:
        return
    if target_user_id == current_user.id:
        return
    admin_depts = (await db.execute(
        select(UserDepartment.department_id).where(
            UserDepartment.user_id == current_user.id,
            UserDepartment.is_deleted == False,
        )
    )).scalars().all()
    target_depts = (await db.execute(
        select(UserDepartment.department_id).where(
            UserDepartment.user_id == target_user_id,
            UserDepartment.is_deleted == False,
        )
    )).scalars().all()
    if not set(target_depts) & set(admin_depts):
        raise HTTPException(
            status_code=403,
            detail="You can only view users within your own department.",
        )


def _role_permission_codes(current_user: User) -> set[str]:
    codes = {
        permission.code
        for permission in (current_user.role.permissions or [])
    } if current_user.role else set()
    codes.update(
        override.permission.code
        for override in (current_user.permission_overrides or [])
        if override.allowed and getattr(override, "permission", None)
    )
    return codes


def _validate_role_permission_update(
    role: Role, current_user: User, requested_codes: list[str]
) -> None:
    if not current_user.role or current_user.role.name == SUPER_ADMIN_ROLE:
        return
    if role.name in {SUPER_ADMIN_ROLE, "Admin"}:
        raise HTTPException(
            status_code=403,
            detail="Only Super Admins can manage administrative role permissions.",
        )
    disallowed = set(requested_codes) - _role_permission_codes(current_user)
    if disallowed:
        raise HTTPException(
            status_code=403,
            detail=f"You cannot grant permissions you don't have: {', '.join(sorted(disallowed))}",
        )


async def _scope_permission_users(db: AsyncSession, current_user: User, stmt):
    if not current_user.role or current_user.role.name == SUPER_ADMIN_ROLE:
        return stmt
    admin_depts = (await db.execute(
        select(UserDepartment.department_id).where(
            UserDepartment.user_id == current_user.id,
            UserDepartment.is_deleted == False,
        )
    )).scalars().all()
    if not admin_depts:
        return stmt
    dept_user_ids = (await db.execute(
        select(UserDepartment.user_id).where(
            UserDepartment.department_id.in_(admin_depts),
            UserDepartment.is_deleted == False,
        )
    )).scalars().all()
    return stmt.where(User.id.in_(dept_user_ids))


def _permission_user_item(user: User) -> dict:
    departments = [
        department_link
        for department_link in (user.user_departments or [])
        if department_link.department
    ]
    department_ids = [link.department_id for link in departments]
    return {
        "id": str(user.id),
        "name": f"{user.first_name} {user.last_name}",
        "email": user.email,
        "avatar_url": avatar_url(user.avatar_path, user.avatar_updated_at),
        "initials": avatar_initials(
            f"{user.first_name} {user.last_name}", user.email
        ),
        "avatar_color_index": avatar_color_index(user.id),
        "role_id": str(user.role_id),
        "role_name": user.role.name if user.role else None,
        "department_name": ", ".join(link.department.name for link in departments) or None,
        "department_id": str(department_ids[0]) if department_ids else None,
    }


async def _validate_user_override(
    db: AsyncSession, current_user: User, target: User, payload: UserPermissionOverrideRequest
) -> None:
    if (
        current_user.role
        and current_user.role.name != SUPER_ADMIN_ROLE
        and getattr(target.role, "name", "") in {SUPER_ADMIN_ROLE, "Admin"}
    ):
        raise HTTPException(status_code=403, detail="Only Super Admins can manage administrative accounts.")
    _validate_override_scope(payload)
    await _validate_override_department(db, payload)
    _validate_override_grant(current_user, payload)


def _validate_override_scope(payload: UserPermissionOverrideRequest) -> None:
    if payload.scope not in {"OWN", "DEPARTMENT", "ALL"}:
        raise HTTPException(status_code=422, detail="Scope must be OWN, DEPARTMENT, or ALL.")
    if payload.scope == "DEPARTMENT" and not payload.department_id:
        raise HTTPException(status_code=422, detail="Department is required for department scope.")
    if payload.scope != "DEPARTMENT" and payload.department_id:
        raise HTTPException(status_code=422, detail="Department is only valid with DEPARTMENT scope.")


async def _validate_override_department(
    db: AsyncSession, payload: UserPermissionOverrideRequest
) -> None:
    if payload.department_id:
        department = await db.get(Department, payload.department_id)
        if not department or department.is_deleted:
            raise HTTPException(status_code=404, detail="Department not found.")


def _validate_override_grant(
    current_user: User, payload: UserPermissionOverrideRequest
) -> None:
    if current_user.role and current_user.role.name != SUPER_ADMIN_ROLE:
        from app.core.dependencies import has_effective_permission
        if payload.allowed and not has_effective_permission(current_user, payload.permission_code):
            raise PermissionDeniedException(
                detail="Access Denied. You do not have permission to perform this action."
            )


async def _permission_audit_name_maps(db: AsyncSession, logs: list[AuditLog]):
    actor_ids = {log.user_id for log in logs if log.user_id}
    actor_id_strings = {str(user_id) for user_id in actor_ids}
    target_ids = {log.record_id for log in logs if log.record_id}
    target_uuids = {}
    for target_id in target_ids:
        try:
            target_uuids[target_id] = UUID(str(target_id))
        except (TypeError, ValueError):
            continue
    user_rows = []
    user_ids = actor_ids | set(target_uuids.values())
    if user_ids:
        user_rows = (await db.execute(
            select(
                User.id,
                User.first_name,
                User.last_name,
                User.email,
                User.avatar_path,
                User.avatar_updated_at,
            )
            .where(User.id.in_(user_ids))
        )).all()

    actor_map = {}
    actor_avatar_map = {}
    target_map = {}
    target_avatar_map = {}
    for user_id, first_name, last_name, email, avatar_path, avatar_updated_at in user_rows:
        user_key = str(user_id)
        full_name = f"{first_name} {last_name}"
        user_name = f"{full_name} ({email})"
        user_avatar = {
            "avatar_url": avatar_url(avatar_path, avatar_updated_at),
            "initials": avatar_initials(full_name, email),
            "avatar_color_index": avatar_color_index(user_key),
        }
        if user_key in actor_id_strings:
            actor_map[user_key] = user_name
            actor_avatar_map[user_key] = user_avatar
        if user_key in target_ids:
            target_map[user_key] = user_name
            target_avatar_map[user_key] = user_avatar
    remaining_role_ids = {
        target_uuids[target_id]
        for target_id in target_ids - set(target_map)
        if target_id in target_uuids
    }
    if remaining_role_ids:
        role_rows = (await db.execute(
            select(Role.id, Role.name).where(Role.id.in_(remaining_role_ids))
        )).all()
        target_map.update({str(role_id): f"Role: {name}" for role_id, name in role_rows})
    return actor_map, actor_avatar_map, target_map, target_avatar_map


# =====================================================================
# 1. List all permission codes
# =====================================================================
@router.get("/", response_model=list[PermissionResponse], dependencies=[Depends(admin_only)])
async def list_all_permissions(db: AsyncSession = Depends(get_db)):
    """System အတွင်းရှိ Granular Permission အားလုံးကို ဆွဲထုတ်ရန်"""
    return await permission_repo.get_all_permissions(db)


# =====================================================================
# 2. Role permissions CRUD
# =====================================================================
@router.get("/role/{role_id}", response_model=list[PermissionResponse], dependencies=[Depends(admin_only)])
async def get_permissions_for_role(role_id: UUID, db: AsyncSession = Depends(get_db)):
    """သတ်မှတ်ထားသော Role တစ်ခုတွင် ရရှိထားသည့် Permission များကို ကြည့်ရန်"""
    return await permission_repo.get_role_permissions(db, role_id)


@router.put(
    "/role/{role_id}",
    response_model=list[PermissionResponse],
    dependencies=[Depends(admin_only)],
    responses={403: {"description": "Not authorized to manage these role permissions"}, 404: {"description": "Not Found"}, 422: {"description": "Unprocessable Entity"}},
)
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
    _validate_role_permission_update(role, current_user, payload.permission_codes)

    before = [p.code for p in role.permissions]
    try:
        result = await permission_repo.assign_permissions_to_role(
            db, role_id, payload.permission_codes
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await log_audit_trail(db, current_user.id, "UPDATE_ROLE_PERMISSIONS", "roles", str(role_id),
                          {"permissions": before}, {"permissions": payload.permission_codes})
    await bump_role_permissions_version(role_id)
    return result


# =====================================================================
# 3. List users for the permissions UI
# =====================================================================
@router.get("/users", response_model=list[dict], dependencies=[Depends(admin_only)])
async def list_permission_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_only),
):
    """Return user list with role and department info for the permissions page."""
    stmt = (
        select(User)
        .options(selectinload(User.role), selectinload(User.user_departments).selectinload(UserDepartment.department))
        .where(User.is_deleted == False)
        .order_by(User.first_name, User.last_name)
    )

    stmt = await _scope_permission_users(db, current_user, stmt)

    result = await db.execute(stmt)
    users = result.scalars().unique().all()

    items = []
    for u in users:
        dept_names = []
        dept_id = None
        for ud in (u.user_departments or []):
            if ud.department:
                dept_names.append(ud.department.name)
                dept_id = dept_id or ud.department_id
        items.append({
            "id": str(u.id),
            "name": f"{u.first_name} {u.last_name}",
            "email": u.email,
            "avatar_url": avatar_url(u.avatar_path, u.avatar_updated_at),
            "initials": avatar_initials(f"{u.first_name} {u.last_name}", u.email),
            "avatar_color_index": avatar_color_index(u.id),
            "role_id": str(u.role_id),
            "role_name": u.role.name if u.role else None,
            "department_name": ", ".join(dept_names) if dept_names else None,
            "department_id": str(dept_id) if dept_id else None,
        })
    return items


# =====================================================================
# 4. User overrides (single)
# =====================================================================
@router.get("/user/{user_id}", response_model=list[UserPermissionOverrideResponse], dependencies=[Depends(admin_only)])
async def get_user_overrides(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _assert_can_read_target(db, current_user, user_id)
    rows = (await db.execute(
        select(UserPermissionOverride, Permission)
        .join(Permission, Permission.id == UserPermissionOverride.permission_id)
        .where(UserPermissionOverride.user_id == user_id)
    )).all()
    return [{"permission_code": p.code, "description": p.description, "allowed": o.allowed,
             "scope": o.scope, "department_id": o.department_id} for o, p in rows]


@router.put(
    "/user/{user_id}",
    response_model=list[UserPermissionOverrideResponse],
    responses={403: {"description": "Not authorized to manage permissions for this user"}, 404: {"description": "Not Found"}, 422: {"description": "Unprocessable Entity"}},
)
async def set_user_override(
    user_id: UUID,
    payload: UserPermissionOverrideRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_only),
):
    await _assert_can_manage_target(db, current_user, user_id)

    target = (await db.execute(
        select(User).options(selectinload(User.role)).where(
            User.id == user_id, User.is_deleted.is_(False)
        )
    )).scalar_one_or_none()
    permission = (await db.execute(select(Permission).where(Permission.code == payload.permission_code))).scalar_one_or_none()
    if not target or not permission:
        raise HTTPException(status_code=404, detail="User or permission not found.")
    await _validate_user_override(db, current_user, target, payload)
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
    await bump_user_permissions_version(user_id)
    await log_audit_trail(db, current_user.id, "UPDATE_USER_PERMISSION", "users", str(user_id),
                          old, {"permission_code": payload.permission_code, "allowed": payload.allowed, "scope": payload.scope})
    # E4: get_user_overrides now takes the caller for its read-scope
    # check; the mutation already scoped this target, so pass it
    # through instead of re-checking.
    return await get_user_overrides(user_id, db, current_user)


@router.delete(
    "/user/{user_id}/{permission_code}",
    status_code=204,
    responses={403: {"description": "Not authorized to manage permissions for this user"}},
)
async def delete_user_override(
    user_id: UUID,
    permission_code: str,
    department_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_only),
):
    await _assert_can_manage_target(db, current_user, user_id)

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
        await bump_user_permissions_version(user_id)
        await log_audit_trail(db, current_user.id, "DELETE_USER_PERMISSION", "users", str(user_id),
                              old, None)


# =====================================================================
# 5. 🌟 NEW: Effective permissions (merged role + overrides)
# =====================================================================
@router.get("/user/{user_id}/effective", dependencies=[Depends(admin_only)], responses={404: {"description": "Not Found"}})
async def get_user_effective_permissions(
    user_id: UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the effective permission matrix for a user, merging role and overrides."""
    await _assert_can_read_target(db, current_user, user_id)
    response.headers["Cache-Control"] = "no-store"
    data = await permission_repo.get_user_effective_permissions(db, user_id)
    if data is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return data


# =====================================================================
# 6. 🌟 NEW: Bulk save user overrides
# =====================================================================
@router.put(
    "/user/{user_id}/bulk",
    responses={403: {"description": "Not authorized to manage permissions for this user"}, 404: {"description": "Not Found"}, 422: {"description": "Unprocessable Entity"}},
)
async def bulk_save_user_overrides(
    user_id: UUID,
    payload: BulkUserOverrideRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_only),
):
    """Atomically replace all user permission overrides."""
    await _assert_can_manage_target(db, current_user, user_id)
    target = (await db.execute(
        select(User).options(selectinload(User.role)).where(
            User.id == user_id, User.is_deleted.is_(False)
        )
    )).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if current_user.role and current_user.role.name != SUPER_ADMIN_ROLE:
        if getattr(target.role, "name", "") in {SUPER_ADMIN_ROLE, "Admin"}:
            raise HTTPException(status_code=403, detail="Only Super Admins can manage administrative accounts.")

    # Security: Non-Super-Admin cannot grant permissions they don't have
    if current_user.role and current_user.role.name != SUPER_ADMIN_ROLE:
        from app.core.dependencies import has_effective_permission
        for item in payload.overrides:
            if item.allowed and not has_effective_permission(current_user, item.permission_code):
                raise HTTPException(
                    status_code=403,
                    detail=f"You cannot grant permission you don't have: {item.permission_code}",
                )

    # Fetch old state for audit
    old_overrides = (await db.execute(
        select(UserPermissionOverride, Permission)
        .join(Permission, Permission.id == UserPermissionOverride.permission_id)
        .where(UserPermissionOverride.user_id == user_id)
    )).all()
    old_data = [
        {"code": p.code, "allowed": o.allowed, "scope": o.scope}
        for o, p in old_overrides
    ]

    new_data = [item.model_dump() for item in payload.overrides]
    try:
        saved = await permission_repo.bulk_save_user_overrides(
            db, user_id, new_data
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await log_audit_trail(
        db, current_user.id, "BULK_UPDATE_USER_PERMISSIONS", "users", str(user_id),
        {"overrides": old_data},
        {"overrides": new_data},
    )
    await bump_user_permissions_version(user_id)
    return {"saved": len(saved), "message": "User permission overrides saved successfully."}


# =====================================================================
# 7. 🌟 NEW: Reset user to role defaults
# =====================================================================
@router.delete("/user/{user_id}/all-overrides", status_code=200, responses={403: {"description": "Forbidden"}})
async def reset_user_overrides(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_only),
):
    """Remove all overrides for a user (reset to role defaults)."""
    await _assert_can_manage_target(db, current_user, user_id)

    # Fetch old for audit
    old_overrides = (await db.execute(
        select(UserPermissionOverride, Permission)
        .join(Permission, Permission.id == UserPermissionOverride.permission_id)
        .where(UserPermissionOverride.user_id == user_id)
    )).all()
    old_data = [
        {"code": p.code, "allowed": o.allowed, "scope": o.scope}
        for o, p in old_overrides
    ]

    count = await permission_repo.delete_all_user_overrides(db, user_id)

    if old_data:
        await log_audit_trail(
            db, current_user.id, "RESET_USER_PERMISSIONS", "users", str(user_id),
            {"overrides": old_data}, {"overrides": []},
        )
    await bump_user_permissions_version(user_id)
    return {
        "deleted": count,
        "message": (
            f"{count} user override(s) removed. "
            "The user now inherits role defaults."
            if count
            else "No user overrides were present; role defaults are already active."
        ),
    }


# =====================================================================
# 8. Enriched audit history
# =====================================================================
@router.get("/audit", response_model=list[dict], dependencies=[Depends(admin_only)])
async def permission_audit_history(
    limit: int = Query(100, gt=0, le=500),
    offset: int = Query(0, ge=0),
    action: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Return enriched permission change audit logs with actor/target names."""
    audit_actions = [
        "UPDATE_ROLE_PERMISSIONS", "UPDATE_USER_PERMISSION",
        "DELETE_USER_PERMISSION", "BULK_UPDATE_USER_PERMISSIONS",
        "RESET_USER_PERMISSIONS",
    ]
    stmt = select(AuditLog).where(AuditLog.action.in_(audit_actions))

    if action and action in audit_actions:
        stmt = stmt.where(AuditLog.action == action)

    stmt = stmt.order_by(desc(AuditLog.created_at)).offset(offset).limit(min(limit, 500))
    result = await db.execute(stmt)
    logs = result.scalars().all()

    # Resolve actor and target names in bulk
    actor_map, actor_avatar_map, target_map, target_avatar_map = (
        await _permission_audit_name_maps(db, logs)
    )

    items = []
    for log in logs:
        actor_id_str = str(log.user_id) if log.user_id else None
        items.append({
            "id": str(log.id),
            "action": log.action,
            "actor_id": actor_id_str,
            "actor_name": actor_map.get(actor_id_str, "System"),
            "actor_avatar": actor_avatar_map.get(actor_id_str),
            "target_id": log.record_id,
            "target_name": target_map.get(log.record_id, log.record_id),
            "target_avatar": target_avatar_map.get(log.record_id),
            "old_value": log.old_value,
            "new_value": log.new_value,
            "created_at": f"{log.created_at.isoformat()}Z" if log.created_at else None,
        })
    return items


# =====================================================================
# 9. Current user's own permission map
# =====================================================================
@router.get("/me", response_model=dict)
async def my_permission_map(current_user: User = Depends(get_current_user)):
    """Expose the current user's effective global permissions to the UI."""
    return {
        "user_id": str(current_user.id),
        "role": current_user.role.name if current_user.role else None,
        "permissions": get_permission_map(current_user),
        # Freshness marker for the web tier's context cache — see
        # app.core.permission_versions for the contract.
        "permissions_version": await permissions_version_for(current_user),
    }


# =====================================================================
# 10. Departments list for scope selector
# =====================================================================
@router.get("/departments", response_model=list[dict], dependencies=[Depends(admin_only)])
async def permission_departments(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Department).where(Department.is_deleted == False).order_by(Department.name)
    )).scalars().all()
    return [{"id": str(row.id), "name": row.name} for row in rows]
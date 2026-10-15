from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.modules.departments.models import UserDepartment
from app.modules.users.models import Permission, Role, RolePermission, User, UserPermissionOverride


async def get_all_permissions(db: AsyncSession) -> list[Permission]:
    stmt = select(Permission).order_by(Permission.code)
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_role_permissions(db: AsyncSession, role_id: UUID) -> list[Permission]:
    stmt = select(Permission).join(RolePermission).where(RolePermission.role_id == role_id)
    result = await db.execute(stmt)
    return result.scalars().all()


async def assign_permissions_to_role(db: AsyncSession, role_id: UUID, permission_codes: list[str]):
    permission_codes = list(dict.fromkeys(permission_codes))
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
        valid_codes = {permission.code for permission in valid_permissions}
        unknown_codes = set(permission_codes) - valid_codes
        if unknown_codes:
            raise ValueError(
                f"Unknown permission codes: {', '.join(sorted(unknown_codes))}"
            )

        # 3. RolePermission Pivot Table သို့ အသစ်ပြန်ထည့်မည်
        for perm in valid_permissions:
            new_role_perm = RolePermission(role_id=role_id, permission_id=perm.id)
            db.add(new_role_perm)

    await db.commit()
    return await get_role_permissions(db, role_id)


# =====================================================================
# 🌟 NEW: Effective Permission Computation
# =====================================================================
async def get_user_effective_permissions(
    db: AsyncSession, user_id: UUID
) -> dict | None:
    """Compute the effective permission matrix for a user by merging
    role permissions with user-specific overrides.

    Returns a dict with user info and a list of effective permission items.
    """
    # Fetch user with role
    from sqlalchemy.orm import selectinload
    user = (await db.execute(
        select(User).options(
            selectinload(User.role).selectinload(Role.permissions),
            selectinload(User.permission_overrides).selectinload(UserPermissionOverride.permission),
        ).where(User.id == user_id, User.is_deleted == False)
    )).scalar_one_or_none()

    if not user:
        return None

    # Fetch user department
    ud = (await db.execute(
        select(UserDepartment)
        .options(selectinload(UserDepartment.department))
        .where(UserDepartment.user_id == user_id, UserDepartment.is_deleted == False)
    )).scalar_one_or_none()

    dept_name = ud.department.name if ud and ud.department else None
    dept_id = ud.department_id if ud else None

    # All system permissions
    all_permissions = await get_all_permissions(db)

    # Resolve overrides for the user's current department.  An explicit deny
    # wins over an allow when legacy data contains multiple scoped rows.
    override_map = _effective_overrides(user, dept_id)

    # Build role permission codes set
    role_codes = set()
    if user.role:
        for p in (user.role.permissions or []):
            role_codes.add(p.code)

    # Merge into effective list
    effective = []
    for perm in all_permissions:
        code = perm.code
        override = override_map.get(code)

        effective.append(_permission_entry(perm, override, code in role_codes))

    return {
        "user_id": user.id,
        "user_name": f"{user.first_name} {user.last_name}".strip(),
        "user_email": user.email,
        "role_name": user.role.name if user.role else None,
        "department_name": dept_name,
        "department_id": dept_id,
        "permissions": effective,
    }


def _effective_overrides(
    user: User, department_id: UUID | None
) -> dict[str, UserPermissionOverride]:
    result: dict[str, UserPermissionOverride] = {}
    for override in user.permission_overrides or []:
        permission = getattr(override, "permission", None)
        if not permission or not _override_applies(override, department_id):
            continue
        current = result.get(permission.code)
        if current is None or (
            override.allowed is False and current.allowed is True
        ):
            result[permission.code] = override
    return result


def _override_applies(
    override: UserPermissionOverride, department_id: UUID | None
) -> bool:
    scope = (override.scope or "ALL").upper()
    return scope == "ALL" or (
        scope == "DEPARTMENT"
        and department_id is not None
        and str(override.department_id) == str(department_id)
    )


def _permission_entry(permission, override, granted_by_role: bool) -> dict:
    if override is not None:
        return {
            "code": permission.code,
            "description": permission.description,
            "allowed": override.allowed,
            "source": "override" if override.allowed else "denied_override",
            "scope": override.scope or "ALL",
            "department_id": override.department_id,
        }
    return {
        "code": permission.code,
        "description": permission.description,
        "allowed": granted_by_role,
        "source": "role" if granted_by_role else "default",
        "scope": "ALL",
        "department_id": None,
    }


# =====================================================================
# 🌟 NEW: Bulk save user overrides
# =====================================================================
def _normalize_permission_overrides(overrides: list[dict]) -> list[dict]:
    normalized = []
    seen = set()
    for item in overrides:
        code = item["permission_code"].strip()
        scope = item.get("scope", "ALL").upper()
        department_id = item.get("department_id")
        if scope not in {"OWN", "DEPARTMENT", "ALL"}:
            raise ValueError("Scope must be OWN, DEPARTMENT, or ALL.")
        if scope == "DEPARTMENT" and department_id is None:
            raise ValueError("Department is required for department scope.")
        if scope != "DEPARTMENT" and department_id is not None:
            raise ValueError("Department is only valid with DEPARTMENT scope.")
        key = (code, str(department_id) if department_id else None)
        if key in seen:
            raise ValueError(f"Duplicate permission override: {code}")
        seen.add(key)
        normalized.append({
            "permission_code": code,
            "allowed": bool(item["allowed"]),
            "scope": scope,
            "department_id": department_id,
        })
    return normalized


async def bulk_save_user_overrides(
    db: AsyncSession,
    user_id: UUID,
    overrides: list[dict],
) -> list[dict]:
    """Atomically replace all user permission overrides.

    ``overrides`` is a list of dicts with keys:
      permission_code, allowed, scope, department_id (optional)

    Only items whose state differs from the role baseline are stored; items
    set to "inherit" (no override) are simply omitted / deleted.
    """
    normalized = _normalize_permission_overrides(overrides)

    # Delete existing overrides for this user
    existing = (await db.execute(
        select(UserPermissionOverride).where(UserPermissionOverride.user_id == user_id)
    )).scalars().all()
    for row in existing:
        await db.delete(row)
    await db.flush()

    if not normalized:
        await db.commit()
        return []

    # Resolve permission codes -> IDs
    codes = [o["permission_code"] for o in normalized]
    perm_rows = (await db.execute(
        select(Permission).where(Permission.code.in_(codes))
    )).scalars().all()
    code_to_id = {p.code: p.id for p in perm_rows}
    unknown_codes = set(codes) - set(code_to_id)
    if unknown_codes:
        raise ValueError(
            f"Unknown permission codes: {', '.join(sorted(unknown_codes))}"
        )

    saved = []
    for item in normalized:
        perm_id = code_to_id.get(item["permission_code"])
        if not perm_id:
            continue
        new_override = UserPermissionOverride(
            user_id=user_id,
            permission_id=perm_id,
            allowed=item["allowed"],
            scope=item.get("scope", "ALL"),
            department_id=item.get("department_id"),
        )
        db.add(new_override)
        saved.append(item)

    await db.commit()
    return saved


# =====================================================================
# 🌟 NEW: Delete all overrides for a user
# =====================================================================
async def delete_all_user_overrides(db: AsyncSession, user_id: UUID) -> int:
    """Remove all user-specific permission overrides (reset to role defaults)."""
    existing = (await db.execute(
        select(UserPermissionOverride).where(UserPermissionOverride.user_id == user_id)
    )).scalars().all()
    count = len(existing)
    for row in existing:
        await db.delete(row)
    await db.commit()
    return count


async def purge_user_overrides(db: AsyncSession, user_id: UUID) -> int:
    """Delete overrides as part of a caller-owned transaction."""
    existing = (await db.execute(
        select(UserPermissionOverride).where(UserPermissionOverride.user_id == user_id)
    )).scalars().all()
    for row in existing:
        await db.delete(row)
    await db.flush()
    return len(existing)
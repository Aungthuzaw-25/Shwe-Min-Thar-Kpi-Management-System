from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.database.connection import get_db
from app.core.exceptions import PermissionDeniedException
# 🌟 FIX: Permission များကို တစ်ပါတည်း ဆွဲထုတ်ရန် Role ကိုပါ Import လုပ်ပါသည်
from app.modules.users.models import User, Role, Permission, RolePermission, UserPermissionOverride
from app.modules.departments.models import UserDepartment
from app.modules.auth.models import TokenBlacklist

SUPER_ADMIN_ROLE = "Super Admin"
GLOBAL_SCOPE_ROLES = {"Super Admin", "System Super Admin", "HR"}
LEVEL_RESTRICTED_TASK_PERMISSIONS = {"tasks.approve", "tasks.reject", "tasks.return"}


async def enforce_user_scope(db: AsyncSession, current_user: User, target_user_id) -> None:
    """Allow self access, shared-department access, or explicitly global roles."""
    if current_user.role and current_user.role.name in GLOBAL_SCOPE_ROLES:
        return
    if str(current_user.id) == str(target_user_id):
        return

    rows = await db.execute(
        select(UserDepartment.department_id).where(
            UserDepartment.user_id == current_user.id
        )
    )
    current_departments = {str(department_id) for department_id in rows.scalars().all()}
    target_rows = await db.execute(
        select(UserDepartment.department_id).where(
            UserDepartment.user_id == target_user_id
        )
    )
    target_departments = {str(department_id) for department_id in target_rows.scalars().all()}
    if (
        not current_user.role
        or current_user.role.name not in {"Admin", "Department Head"}
        or not current_departments.intersection(target_departments)
    ):
        raise PermissionDeniedException(
            detail="Access denied for this user's organizational scope."
        )


async def enforce_department_scope(
    db: AsyncSession, current_user: User, department_id
) -> None:
    """Allow global roles or users explicitly assigned to the department."""
    if current_user.role and current_user.role.name in GLOBAL_SCOPE_ROLES:
        return
    result = await db.execute(
        select(UserDepartment.id).where(
            UserDepartment.user_id == current_user.id,
            UserDepartment.department_id == department_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise PermissionDeniedException(
            detail="Access denied for this department scope."
        )


def has_effective_permission(
    user: User,
    permission_code: str,
    *,
    owner_id=None,
    department_id=None,
) -> bool:
    """Evaluate one permission using deny-over-role precedence.

    Overrides are deliberately evaluated before role grants.  A matching
    explicit deny always wins; an explicit allow can grant a permission that
    is not present on the user's role.  Unknown permissions are denied.
    """
    if user.role and user.role.name == SUPER_ADMIN_ROLE:
        return True

    matching = []
    for override in getattr(user, "permission_overrides", []) or []:
        permission = getattr(override, "permission", None)
        if not permission or permission.code != permission_code:
            continue
        scope = (override.scope or "ALL").upper()
        if scope == "ALL":
            matching.append(override)
        elif scope == "DEPARTMENT" and department_id is not None:
            if str(override.department_id) == str(department_id):
                matching.append(override)
        elif scope == "OWN" and owner_id is not None:
            if str(owner_id) == str(user.id):
                matching.append(override)

    # A deny is stronger than every role grant (and than an allow at another
    # scope), which makes accidental data exposure fail closed.
    if any(override.allowed is False for override in matching):
        return False
    if any(override.allowed is True for override in matching):
        return True

    role_permissions = getattr(user.role, "permissions", []) if user.role else []
    has_role_permission = any(
        getattr(permission, "code", None) == permission_code
        for permission in role_permissions
    )
    if not has_role_permission:
        return False

    # L0/L1 can review the department workflow; L2 remains a submitter.
    # Explicit user overrides above still take precedence over this level gate.
    if permission_code in LEVEL_RESTRICTED_TASK_PERMISSIONS:
        department_links = getattr(user, "user_departments", []) or []
        levels = {
            str(getattr(link, "user_level", "")).upper().replace("LEVEL ", "L")
            for link in department_links
            if not getattr(link, "is_deleted", False)
        }
        return bool(levels.intersection({"L0", "L1"}))

    return True


def enforce_permission(
    user: User,
    permission_code: str,
    *,
    owner_id=None,
    department_id=None,
) -> User:
    if not has_effective_permission(
        user, permission_code, owner_id=owner_id, department_id=department_id
    ):
        raise PermissionDeniedException(
            detail="Access Denied. You do not have permission to perform this action."
        )
    return user


def get_permission_map(user: User) -> dict[str, bool]:
    """Return the effective global permission map for templates and clients."""
    codes = {
        getattr(permission, "code", None)
        for permission in (getattr(user.role, "permissions", []) if user.role else [])
    }
    codes.update(
        getattr(getattr(override, "permission", None), "code", None)
        for override in getattr(user, "permission_overrides", []) or []
    )
    return {
        code: has_effective_permission(user, code)
        for code in sorted(code for code in codes if code)
    }

# 🌟 KEY FIX: Swagger UI မှ Auth ဝင်ရန် လမ်းကြောင်းကို swagger-login သို့ ပြောင်းထားပါသည်
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/swagger-login", auto_error=False)

async def get_current_user(
    request: Request, 
    token: str = Depends(oauth2_scheme), 
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Cookie (Web), Header (Mobile App), နှင့် Swagger UI အားလုံးကို Support ပေးသော 
    Universal Authentication Function
    """
    # ၁။ Swagger UI သို့မဟုတ် Authorization Header မှ Token ကို အရင်ရှာမည်
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
        else:
            # ၂။ မရှိပါက Browser ၏ HttpOnly Cookie မှ access_token ကို ရှာမည်
            cookie_token = request.cookies.get("access_token")
            if cookie_token:
                token = cookie_token.removeprefix("Bearer ").strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Not authenticated. Token missing."
        )

    # 🌟 SECURITY & PERFORMANCE FIX: Hacker မှ Token အမှားများဖြင့် DB ကို DoS မလုပ်နိုင်ရန် JWT ကို အရင် Decode လုပ်မည်
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, 
                detail="Invalid token payload."
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Token expired or invalid."
        )

    # Decode အောင်မြင်မှသာ Blacklist ဝင်နေသလား စစ်ဆေးမည်
    is_blacklisted = await db.execute(select(TokenBlacklist).where(TokenBlacklist.token == token))
    if is_blacklisted.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Token has been revoked. Please log in again."
        )

    # 🌟 PERFORMANCE FIX (DBA): User ကို ဆွဲထုတ်စဉ် Role နှင့် ၎င်း၏ Permissions များကို 
    # တစ်ပါတည်း (Eager Load) ဆွဲထုတ်ထားခြင်းဖြင့် RequirePermission တွင် DB ထပ်ခေါ်စရာ မလိုတော့ပါ။
    stmt = select(User).options(
        selectinload(User.role).selectinload(Role.permissions),
        selectinload(User.permission_overrides).selectinload(UserPermissionOverride.permission),
        selectinload(User.user_departments),
    ).where(
        User.email == email, 
        User.is_deleted == False
    )
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="User not found or account disabled."
        )
        
    if user.is_locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Account is temporarily locked."
        )
        
    return user


# =====================================================================
# 🌟 ROLE-BASED ACCESS CONTROL (RBAC)
# =====================================================================
class RoleChecker:
    def __init__(self, allowed_roles: list[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: User = Depends(get_current_user)):
        if not user.role or user.role.name not in self.allowed_roles:
            raise PermissionDeniedException(
                detail="Access Denied. You do not have permission to perform this action."
            )
        return user


# =====================================================================
# 🌟 GRANULAR PERMISSION SYSTEM (Optimized for High Performance)
# =====================================================================
class RequirePermission:
    """
    တိကျသော လုပ်ပိုင်ခွင့်များ (ဥပမာ - 'kpi.approve', 'report.export') ကို စစ်ဆေးရန်။
    Usage: @router.get("/", dependencies=[Depends(RequirePermission(["kpi.approve"]))])
    """
    def __init__(self, required_permissions: list[str]):
        self.required_permissions = required_permissions

    # 🌟 FIX: DB ကို ထပ်မံ Query လုပ်စရာ မလိုတော့ဘဲ get_current_user မှရလာသော Data ပေါ်မှ တိုက်ရိုက် စစ်ဆေးမည်
    async def __call__(
        self,
        request: Request,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Check permissions and infer resource scope from common route IDs."""
        owner_id = None
        department_id = None
        params = request.path_params
        owner_param = next(
            (params.get(name) for name in ("owner_id", "target_user_id", "user_id")
             if params.get(name) is not None),
            None,
        )
        if owner_param is not None:
            owner_id = owner_param
            try:
                from app.modules.departments.models import UserDepartment
                row = (await db.execute(
                    select(UserDepartment.department_id).where(
                        UserDepartment.user_id == owner_param,
                        UserDepartment.is_deleted == False,
                    )
                )).first()
                department_id = row[0] if row else None
            except Exception:
                department_id = None

        department_id = (
            params.get("department_id")
            or params.get("dept_id")
            or department_id
        )

        # Resolve task/KPI/batch resources so OWN and DEPARTMENT overrides work
        # on routes that expose only a resource UUID.
        try:
            resource_id = params.get("task_id")
            if resource_id:
                from app.modules.tasks.models.task import Task
                from app.modules.departments.models import UserDepartment
                task = await db.get(Task, resource_id)
                if task:
                    owner_id = task.owner_id
                    row = (await db.execute(
                        select(UserDepartment.department_id).where(
                            UserDepartment.user_id == task.owner_id,
                            UserDepartment.is_deleted == False,
                        )
                    )).first()
                    department_id = department_id or (row[0] if row else None)
            resource_id = params.get("record_id")
            if resource_id:
                from app.modules.kpi.models import KPIRecord
                record = await db.get(KPIRecord, resource_id)
                if record:
                    owner_id = record.user_id
                    department_id = department_id or record.department_id
            resource_id = params.get("batch_id")
            if resource_id:
                from app.modules.tasks.models.submission_batch import SubmissionBatch
                batch = await db.get(SubmissionBatch, resource_id)
                if batch:
                    owner_id = batch.submitter_id
        except Exception:
            # Scope resolution must never turn a malformed resource into an
            # authorization bypass; unresolved scoped overrides simply don't
            # match and the normal role decision remains fail-closed.
            pass

        # Required permissions are OR semantics.  A deny only vetoes its own
        # permission; another explicitly granted required permission may still
        # satisfy an endpoint declaring alternatives.
        for permission_code in self.required_permissions:
            if has_effective_permission(
                current_user,
                permission_code,
                owner_id=owner_id,
                department_id=department_id,
            ):
                return current_user
        raise PermissionDeniedException(
            detail="Access Denied. You do not have permission to perform this action."
        )
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError as JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import exists, select
from sqlalchemy.orm import joinedload

from app.core import security
from app.core.exceptions import PermissionDeniedException
from app.database.connection import get_db
from app.modules.auth.models import AuthSession, TokenBlacklist
from app.modules.departments.models import UserDepartment

# 🌟 FIX: Permission များကို တစ်ပါတည်း ဆွဲထုတ်ရန် Role ကိုပါ Import လုပ်ပါသည်
from app.modules.users.models import Role, User, UserPermissionOverride

SUPER_ADMIN_ROLE = "Super Admin"
GLOBAL_SCOPE_ROLES = {"Super Admin", "HR"}
LEVEL_RESTRICTED_TASK_PERMISSIONS = {"tasks.approve", "tasks.reject", "tasks.return"}
ACCESS_DENIED_MESSAGE = "Access Denied. You do not have permission to perform this action."


async def get_authorized_department_ids(
    db: AsyncSession, current_user: User
) -> set[str] | None:
    """Return active department memberships, or ``None`` for global roles."""
    if current_user.role and current_user.role.name in GLOBAL_SCOPE_ROLES:
        return None
    rows = await db.execute(
        select(UserDepartment.department_id).where(
            UserDepartment.user_id == current_user.id,
            UserDepartment.is_deleted.is_(False),
        )
    )
    return {str(department_id) for department_id in rows.scalars().all()}


async def enforce_user_scope(db: AsyncSession, current_user: User, target_user_id) -> None:
    """Allow self access, shared-department access, or explicitly global roles."""
    if current_user.role and current_user.role.name in GLOBAL_SCOPE_ROLES:
        return
    if str(current_user.id) == str(target_user_id):
        return

    rows = await db.execute(
        select(UserDepartment.department_id).where(
            UserDepartment.user_id == current_user.id,
            UserDepartment.is_deleted.is_(False)
        )
    )
    current_departments = {str(department_id) for department_id in rows.scalars().all()}
    target_rows = await db.execute(
        select(UserDepartment.department_id).where(
            UserDepartment.user_id == target_user_id,
            UserDepartment.is_deleted.is_(False)
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
    authorized_department_ids = await get_authorized_department_ids(
        db, current_user
    )
    if authorized_department_ids is None:
        return
    if str(department_id) not in authorized_department_ids:
        raise PermissionDeniedException(
            detail="Access denied for this department scope."
        )


def _matching_permission_overrides(
    user: User, permission_code: str, *, owner_id=None, department_id=None
) -> list:
    matching = []
    for override in getattr(user, "permission_overrides", []) or []:
        permission = getattr(override, "permission", None)
        if not permission or permission.code != permission_code:
            continue
        scope = (override.scope or "ALL").upper()
        if _override_matches_scope(
            user, override, scope, owner_id=owner_id, department_id=department_id
        ):
            matching.append(override)
    return matching


def _override_matches_scope(
    user: User, override, scope: str, *, owner_id=None, department_id=None
) -> bool:
    if scope == "ALL":
        return True
    if scope == "DEPARTMENT":
        return (
            department_id is not None
            and str(override.department_id) == str(department_id)
        )
    if scope == "OWN":
        return owner_id is not None and str(owner_id) == str(user.id)
    return False


def _has_role_permission(user: User, permission_code: str) -> bool:
    role_permissions = getattr(user.role, "permissions", []) if user.role else []
    return any(
        getattr(permission, "code", None) == permission_code
        for permission in role_permissions
    )


def _can_review_task_permission(user: User) -> bool:
    department_links = getattr(user, "user_departments", []) or []
    levels = {
        str(getattr(link, "user_level", "")).upper().replace("LEVEL ", "L")
        for link in department_links
        if not getattr(link, "is_deleted", False)
    }
    return bool(levels.intersection({"L0", "L1"}))


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

    matching = _matching_permission_overrides(
        user, permission_code, owner_id=owner_id, department_id=department_id
    )

    # A deny is stronger than every role grant (and than an allow at another
    # scope), which makes accidental data exposure fail closed.
    if any(override.allowed is False for override in matching):
        return False
    if any(override.allowed is True for override in matching):
        return True

    if not _has_role_permission(user, permission_code):
        return False

    # L0/L1 can review the department workflow; L2 remains a submitter.
    # Explicit user overrides above still take precedence over this level gate.
    if permission_code in LEVEL_RESTRICTED_TASK_PERMISSIONS:
        return _can_review_task_permission(user)

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
            detail=ACCESS_DENIED_MESSAGE
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


def _request_access_token(request: Request, token: str | None) -> str | None:
    if token:
        return token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ")[1]
    cookie_token = request.cookies.get("access_token")
    return cookie_token.removeprefix("Bearer ").strip() if cookie_token else None


def _decode_access_token(token: str) -> tuple[str, UUID, UUID]:
    try:
        payload = security.decode_token(token, "access", require_session=True)
        return payload["sub"], UUID(payload["sid"]), UUID(payload["user_id"])
    except (JWTError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired or invalid.",
        ) from exc


async def _load_authentication_state(
    db: AsyncSession,
    token: str,
    email: str,
    session_id: UUID,
    session_user_id: UUID,
) -> tuple[User | None, bool, bool]:
    now = datetime.now(UTC).replace(tzinfo=None)
    blacklist_exists = exists(
        select(TokenBlacklist.id).where(
            TokenBlacklist.token_hash == security.token_digest(token)
        )
    )
    session_exists = exists(
        select(AuthSession.id).where(
            AuthSession.id == session_id,
            AuthSession.user_id == session_user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
    )
    stmt = (
        select(
            User,
            blacklist_exists.label("token_blacklisted"),
            session_exists.label("session_valid"),
        )
        .options(
            joinedload(User.role).joinedload(Role.permissions),
            joinedload(User.permission_overrides).joinedload(
                UserPermissionOverride.permission
            ),
            joinedload(User.user_departments),
        )
        .where(User.email == email)
    )
    result = await db.execute(stmt)
    row = result.unique().one_or_none()

    if row is None:
        # Preserve blacklist/session error precedence when no user matches.
        state_result = await db.execute(
            select(
                blacklist_exists.label("token_blacklisted"),
                session_exists.label("session_valid"),
            )
        )
        is_blacklisted, valid_session = state_result.one()
        return None, is_blacklisted, valid_session
    user, is_blacklisted, valid_session = row
    return user, is_blacklisted, valid_session


def _validate_authenticated_user(
    user: User | None, is_blacklisted: bool, valid_session: bool
) -> User:
    if is_blacklisted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked. Please log in again.",
        )
    if not valid_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked or expired.",
        )
    if not user or user.is_deleted or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or account disabled.",
        )
    if user.is_locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is temporarily locked.",
        )
    return user


async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Cookie (Web), Header (Mobile App), နှင့် Swagger UI အားလုံးကို Support ပေးသော
    Universal Authentication Function
    """
    token = _request_access_token(request, token)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Token missing."
        )

    email, session_id, session_user_id = _decode_access_token(token)
    user, is_blacklisted, valid_session = await _load_authentication_state(
        db, token, email, session_id, session_user_id
    )
    return _validate_authenticated_user(user, is_blacklisted, valid_session)


# =====================================================================
# 🌟 ROLE-BASED ACCESS CONTROL (RBAC)
# =====================================================================
class RoleChecker:
    def __init__(self, allowed_roles: list[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: User = Depends(get_current_user)):
        if not user.role or user.role.name not in self.allowed_roles:
            raise PermissionDeniedException(
                detail=ACCESS_DENIED_MESSAGE
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

    async def _resolve_owner_scope(self, db: AsyncSession, owner_param):
        if owner_param is None:
            return None, None
        try:
            from app.modules.departments.models import UserDepartment

            row = (
                await db.execute(
                    select(UserDepartment.department_id).where(
                        UserDepartment.user_id == owner_param,
                        UserDepartment.is_deleted == False,
                    )
                )
            ).first()
            return owner_param, row[0] if row else None
        except Exception:
            return owner_param, None

    async def _resolve_resource_scope(
        self, db: AsyncSession, params, owner_id, department_id
    ):
        try:
            resource_id = params.get("task_id")
            if resource_id:
                from app.modules.departments.models import UserDepartment
                from app.modules.tasks.models.task import Task

                task = await db.get(Task, resource_id)
                if task:
                    owner_id = task.owner_id
                    row = (
                        await db.execute(
                            select(UserDepartment.department_id).where(
                                UserDepartment.user_id == task.owner_id,
                                UserDepartment.is_deleted == False,
                            )
                        )
                    ).first()
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
        except Exception:  # noqa: S110 - malformed scope hints remain fail-closed
            # Unresolved scope hints cannot satisfy OWN/DEPARTMENT overrides.
            pass
        return owner_id, department_id

    # 🌟 FIX: DB ကို ထပ်မံ Query လုပ်စရာ မလိုတော့ဘဲ get_current_user မှရလာသော Data ပေါ်မှ တိုက်ရိုက် စစ်ဆေးမည်
    async def _resolve_owner_department(self, db: AsyncSession, owner_param):
        if owner_param is None:
            return None
        try:
            from app.modules.departments.models import UserDepartment

            row = (
                await db.execute(
                    select(UserDepartment.department_id).where(
                        UserDepartment.user_id == owner_param,
                        UserDepartment.is_deleted.is_(False),
                    )
                )
            ).first()
            return row[0] if row else None
        except Exception:
            return None

    async def _resolve_task_scope(self, db: AsyncSession, resource_id, department_id):
        if not resource_id:
            return None, department_id
        from app.modules.departments.models import UserDepartment
        from app.modules.tasks.models.task import Task

        task = await db.get(Task, resource_id)
        if not task:
            return None, department_id
        row = (
            await db.execute(
                select(UserDepartment.department_id).where(
                    UserDepartment.user_id == task.owner_id,
                    UserDepartment.is_deleted.is_(False),
                )
            )
        ).first()
        return task.owner_id, department_id or (row[0] if row else None)

    async def _resolve_record_scope(self, db: AsyncSession, resource_id, department_id):
        if not resource_id:
            return None, department_id
        from app.modules.kpi.models import KPIRecord

        record = await db.get(KPIRecord, resource_id)
        if not record:
            return None, department_id
        return record.user_id, department_id or record.department_id

    async def _resolve_batch_owner(self, db: AsyncSession, resource_id):
        if not resource_id:
            return None
        from app.modules.tasks.models.submission_batch import SubmissionBatch

        batch = await db.get(SubmissionBatch, resource_id)
        return batch.submitter_id if batch else None

    async def _resolve_resource_scope(self, params, db, owner_id, department_id):
        try:
            task_owner, department_id = await self._resolve_task_scope(
                db, params.get("task_id"), department_id
            )
            if task_owner is not None:
                owner_id = task_owner
            record_owner, department_id = await self._resolve_record_scope(
                db, params.get("record_id"), department_id
            )
            if record_owner is not None:
                owner_id = record_owner
            batch_owner = await self._resolve_batch_owner(db, params.get("batch_id"))
            if batch_owner is not None:
                owner_id = batch_owner
        except Exception:  # noqa: S110 - malformed scope hints remain fail-closed
            # Unresolved scoped overrides do not match; role decisions still apply.
            pass
        return owner_id, department_id

    async def __call__(
        self,
        request: Request,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Check permissions and infer resource scope from common route IDs."""
        params = request.path_params
        owner_param = next(
            (params.get(name) for name in ("owner_id", "target_user_id", "user_id")
             if params.get(name) is not None),
            None,
        )
        owner_id = owner_param
        department_id = await self._resolve_owner_department(db, owner_param)
        department_id = (
            params.get("department_id")
            or params.get("dept_id")
            or department_id
        )
        owner_id, department_id = await self._resolve_resource_scope(
            params, db, owner_id, department_id
        )

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
            detail=ACCESS_DENIED_MESSAGE
        )
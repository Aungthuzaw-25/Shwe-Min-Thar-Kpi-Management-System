import csv
import io
import logging
import secrets
import traceback

logger = logging.getLogger(__name__)
from datetime import UTC, datetime
from uuid import UUID

from fastapi import (  # 🌟 PERFORMANCE FIX: Added Query
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy import func, or_, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import (
    GLOBAL_SCOPE_ROLES,
    enforce_department_scope,
    enforce_permission,
    enforce_user_scope,
    get_current_user,
)
from app.core.permission_versions import bump_user_permissions_version
from app.core.password_policy import get_password_policy, validate_password_strength
from app.core.security import get_password_hash_async
from app.core.idempotency import idempotency_guard
from app.database.connection import get_db
from app.modules.departments.models import Department, UserDepartment
from app.modules.tasks.models.task import Task
from app.modules.tasks.models.task_history import TaskHistory
from app.modules.users import password_repo, permission_repo
from app.modules.users import repo as user_repo
from app.modules.users.presence import presence_for_users

# 🌟 BUG FIX: Role Model ကို models.py မှ တိုက်ရိုက်ယူပါမည် (roles.py အဟောင်းကို မသုံးပါ)
from app.models.audit_log import AuditLog
from app.modules.system import retention_service as retention
from app.modules.users.models import Role, User, UserStatus
from app.modules.users.password_schemas import PasswordResetAdmin
from app.modules.users.schemas import (
    RoleResponse,
    SuperAdminCreate,
    SuperAdminUpdate,
    UserCreate,
    UserDeptResponse,
    UserLeaveRequest,
    UserOffboardRequest,
    UserResponse,
    UserUpdate,
)
from app.services.activity_service import record_activity
from app.services.authority_notice import stage_super_admin_notice
from app.utils.audit import log_audit_trail

router = APIRouter()
SUPER_ADMIN_ROLE_NAME = "Super Admin"
SUPER_ADMIN_ROLE_NAMES = {SUPER_ADMIN_ROLE_NAME}
VALID_USER_LEVELS = {'L0', 'L1', 'L2'}
PERM_USERS_VIEW = "users.view"
ERR_SUPER_ADMIN_NOT_FOUND = "Super Admin not found."
ERR_EMAIL_ALREADY_EXISTS = "An account with this email already exists."
ERR_NOT_SUPER_ADMIN = "This user is not a Super Admin account."
ERR_USER_NOT_FOUND = "User not found."


async def _get_process_trend(db: AsyncSession, owner_ids):
    """Return daily process-history counts for the tracking chart."""
    trend_stmt = (
        select(func.date(TaskHistory.created_at).label("day"), TaskHistory.action, func.count(TaskHistory.id))
        .join(Task, TaskHistory.task_id == Task.id)
        .where(Task.owner_id.in_(owner_ids), Task.is_deleted == False, TaskHistory.is_deleted == False)
        .group_by(func.date(TaskHistory.created_at), TaskHistory.action)
        .order_by(func.date(TaskHistory.created_at))
    )
    rows = (await db.execute(trend_stmt)).all()
    by_day = {}
    for day, action, count in rows:
        bucket = by_day.setdefault(str(day), {"completed": 0, "in_progress": 0, "draft": 0, "failed": 0})
        action_name = str(action).upper()
        if any(value in action_name for value in ("APPROVED", "VERIFIED", "COMPLETED")):
            bucket["completed"] += int(count)
        elif "REJECTED" in action_name:
            bucket["failed"] += int(count)
        elif any(value in action_name for value in ("SUBMITTED", "PENDING", "REVIEW")):
            bucket["in_progress"] += int(count)
        else:
            bucket["draft"] += int(count)
    return [
        {"label": day, **values}
        for day, values in list(by_day.items())[-30:]
    ]


def normalize_user_level(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper().replace('LEVEL ', 'L')
    if normalized not in VALID_USER_LEVELS:
        raise HTTPException(status_code=422, detail='user_level must be L0, L1, or L2.')
    return normalized


def require_super_admin(current_user: User) -> None:
    if not current_user or not current_user.role or current_user.role.name not in SUPER_ADMIN_ROLE_NAMES:
        raise HTTPException(status_code=403, detail='You do not have permission to perform this action.')


def ensure_user_deletion_allowed(target_user: User, current_user: User) -> None:
    if target_user.id == current_user.id:
        raise HTTPException(status_code=400, detail='You cannot delete your own account.')
    if getattr(target_user.role, 'name', '') in SUPER_ADMIN_ROLE_NAMES:
        raise HTTPException(status_code=403, detail='Super Admin accounts cannot be deleted.')


async def _detach_department_head(db: AsyncSession, user_id: UUID) -> None:
    """Clear departments.head_id pointing at a user (ORM update —
    raw text() updates with str(uuid) params silently match zero
    rows on some databases, which is how this detach once failed
    QA; ORM binds the UUID properly everywhere)."""
    from app.modules.departments.models import Department

    await db.execute(
        update(Department).where(Department.head_id == user_id).values(head_id=None)
    )


async def revoke_user_authentication(db: AsyncSession, user_id: UUID) -> None:
    """Disable every authentication path without deleting business history."""
    now = datetime.now(UTC).replace(tzinfo=None)
    await db.execute(
        text(
            "UPDATE auth_sessions SET revoked_at = :now "
            "WHERE user_id = :uid AND revoked_at IS NULL"
        ),
        {"now": now, "uid": str(user_id)},
    )
    await db.execute(
        text(
            "UPDATE oauth_accounts SET revoked_at = :now "
            "WHERE user_id = :uid AND revoked_at IS NULL"
        ),
        {"now": now, "uid": str(user_id)},
    )


async def get_super_admin_role(db: AsyncSession):
    role_stmt = select(Role).where(Role.name == SUPER_ADMIN_ROLE_NAME)
    role_result = await db.execute(role_stmt)
    role = role_result.scalar_one_or_none()
    if role is None:
        role_stmt = select(Role).where(Role.name.in_(list(SUPER_ADMIN_ROLE_NAMES)))
        role_result = await db.execute(role_stmt)
        role = role_result.scalars().first()
    return role


async def count_active_super_admins(db: AsyncSession, exclude_user_id: UUID | None = None) -> int:
    stmt = select(func.count(User.id)).select_from(User).join(Role, User.role_id == Role.id).where(
        User.is_deleted == False,
        User.is_active == True,
        Role.name.in_(list(SUPER_ADMIN_ROLE_NAMES))
    )
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


def serialize_user_obj(u: User, presence: dict | None = None) -> UserResponse:
    role_data = RoleResponse(id=u.role.id, name=u.role.name) if u.role else None
    presence = presence or {}

    pos_data = None
    if getattr(u, 'position', None):
        pos_data = {"id": u.position.id, "name": u.position.name}
        
    departments_data = [
        UserDeptResponse(
            id=ud.department.id,
            name=ud.department.name,
            level=ud.user_level,
            is_head=(str(ud.department.head_id) == str(u.id)) if ud.department.head_id else False
        )
        for ud in getattr(u, 'user_departments', []) if ud.department
    ]
    
    return UserResponse(
        id=u.id,
        first_name=u.first_name,
        last_name=u.last_name,
        email=u.email,
        position=pos_data, 
        region_town=u.region_town,
        is_active=u.is_active,
        role=role_data,
        departments=departments_data,
        created_at=getattr(u, 'created_at', None),
        last_login=presence.get('last_login_at') or getattr(u, 'last_login', None),
        presence_status=presence.get('presence_status'),
        last_active_at=presence.get('last_active_at'),
        last_offline_at=presence.get('last_offline_at')
    )


def _provided_user_password(payload: UserCreate) -> str | None:
    values = payload.model_dump()
    return (
        getattr(payload, "password", None)
        or getattr(payload, "temporary_password", None)
        or getattr(payload, "temp_password", None)
        or values.get("password")
        or values.get("temporary_password")
    )


async def _ensure_user_email_available(db: AsyncSession, email: str) -> None:
    existing = (await db.execute(select(User).where(User.email == email))).scalars().first()
    if not existing:
        return
    if getattr(existing, "is_deleted", False):
        raise HTTPException(
            status_code=400,
            detail=f"အကောင့်ဖန်တီး၍မရပါ။ '{email}' သည် ဖျက်သိမ်းထားသော အကောင့်ဟောင်းတစ်ခုဖြစ်သည်။ ကျေးဇူးပြု၍ အသစ်မဖန်တီးဘဲ အကောင့်ဟောင်းကိုသာ Trash (Deleted List) မှတဆင့် Restore (ပြန်လည်အသက်သွင်း) ပေးပါ။",
        )
    raise HTTPException(status_code=400, detail="Email address is already registered and currently active.")


async def _assign_new_user_department(
    db: AsyncSession, user: User, department_id, user_level: str
) -> None:
    if not department_id:
        return
    result = await db.execute(
        select(Department).where(
            Department.id == department_id,
            Department.is_deleted == False,
        )
    )
    department = result.scalar_one_or_none()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found.")
    db.add(
        UserDepartment(
            user_id=user.id,
            department_id=department_id,
            user_level=user_level,
        )
    )
    if user_level == "L0":
        department.head_id = user.id
        db.add(department)


@router.post('/', dependencies=[Depends(idempotency_guard)], response_model=UserResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 422: {"description": "Unprocessable Entity"}, 500: {"description": "Internal Server Error"}})
async def create_new_user(payload: UserCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, "users.create")
    role_req = await db.execute(select(Role).where(Role.id == payload.role_id))
    target_role = role_req.scalar_one_or_none()
    if not target_role:
        raise HTTPException(status_code=404, detail="Target role not found.")
    if current_user.role.name != SUPER_ADMIN_ROLE_NAME and target_role.name in {SUPER_ADMIN_ROLE_NAME, 'Admin'}:
        raise HTTPException(status_code=403, detail='Only Super Admins can manage administrative accounts.')

    await _ensure_user_email_available(db, payload.email)
    user_data = payload.model_dump(exclude={"department_id", "user_level", "password", "temporary_password", "temp_password"}, exclude_unset=True)
    provided_password = _provided_user_password(payload)
    if not provided_password:
        raise HTTPException(status_code=400, detail='Password (Temporary Password) is required.')
        
    dept_id = getattr(payload, 'department_id', None)
    if dept_id:
        await enforce_department_scope(db, current_user, dept_id)
    user_level = normalize_user_level(getattr(payload, 'user_level', 'L2') or 'L2')
    
    try:
        validate_password_strength(provided_password, **(await get_password_policy(db)))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    user_data['hashed_password'] = await get_password_hash_async(provided_password)
    user_data['local_password_enabled'] = True
    user_data['must_change_password'] = True 
    
    try:
        new_user = await user_repo.create_user(db, user_data, commit=False)
        await password_repo.add_password_history(
            db, new_user.id, new_user.hashed_password, commit=False
        )
        
        await _assign_new_user_department(db, new_user, dept_id, user_level)
        record_activity(
            db,
            actor_id=current_user.id,
            action="USER_CREATED",
            entity_type="user",
            entity_id=new_user.id,
            description=f"Created user {new_user.first_name} {new_user.last_name}".strip(),
        )
        if target_role.name in {SUPER_ADMIN_ROLE_NAME, 'Admin'}:
            await stage_super_admin_notice(
                db,
                actor_id=current_user.id,
                title="Administrative account created",
                message=f"{current_user.first_name} {current_user.last_name} created "
                f"{target_role.name} account {new_user.email}.",
            )
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {str(e)}")
        
    db.expunge(new_user)
    complete_user = await user_repo.get_user_by_id(db, new_user.id)

    resp_data = serialize_user_obj(complete_user).model_dump()
    return resp_data


# ======================================================================
# Bulk user import (CSV) — KPI-upload pattern: valid rows are created,
# invalid rows are reported per row, nothing is all-or-nothing.
# Columns: email, first_name, last_name, role, department,
# user_level, password (optional). Role/department match by NAME.
# ======================================================================
BULK_IMPORT_MAX_ROWS = 1000
BULK_IMPORT_REQUIRED_COLUMNS = ("email", "first_name", "last_name", "role")


@router.post('/bulk-import', responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 422: {"description": "Unprocessable Entity"}})
async def bulk_import_users(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce_permission(current_user, "users.create")
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="A .csv file is required.")
    try:
        text = (await file.read()).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="CSV must be UTF-8 encoded.")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="CSV has no header row.")
    header_map = {(name or "").strip().lower(): name for name in reader.fieldnames}
    missing = [c for c in BULK_IMPORT_REQUIRED_COLUMNS if c not in header_map]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing CSV columns: {', '.join(missing)}.")
    rows = list(reader)
    if len(rows) > BULK_IMPORT_MAX_ROWS:
        raise HTTPException(status_code=422, detail=f"At most {BULK_IMPORT_MAX_ROWS} rows per import.")

    roles = (await db.execute(select(Role))).scalars().all()
    roles_by_name = {role.name.strip().lower(): role for role in roles}
    departments = (await db.execute(
        select(Department).where(Department.is_deleted == False)
    )).scalars().all()
    departments_by_name = {dept.name.strip().lower(): dept for dept in departments}
    policy = await get_password_policy(db)
    is_super_admin = current_user.role and current_user.role.name == SUPER_ADMIN_ROLE_NAME

    results: list[dict] = []
    seen_emails: set[str] = set()
    created = 0
    for index, raw_row in enumerate(rows, start=2):  # row 1 is the header
        row = {
            (key or "").strip().lower(): (value.strip() if isinstance(value, str) else value)
            for key, value in raw_row.items()
            if key is not None
        }
        email = row.get("email") or ""
        try:
            if not email or "@" not in email or "." not in email.split("@")[-1]:
                raise HTTPException(status_code=422, detail="Invalid email address.")
            if email.lower() in seen_emails:
                raise HTTPException(status_code=422, detail="Duplicate email within the file.")
            seen_emails.add(email.lower())
            first_name = row.get("first_name") or ""
            last_name = row.get("last_name") or ""
            if not first_name or not last_name:
                raise HTTPException(status_code=422, detail="first_name and last_name are required.")
            role_name = row.get("role") or ""
            target_role = roles_by_name.get(role_name.strip().lower())
            if not target_role:
                raise HTTPException(status_code=422, detail=f"Unknown role: {role_name}.")
            if target_role.name == SUPER_ADMIN_ROLE_NAME:
                raise HTTPException(status_code=403, detail="Super Admin accounts cannot be bulk-imported.")
            if not is_super_admin and target_role.name == "Admin":
                raise HTTPException(status_code=403, detail="Only Super Admins can manage administrative accounts.")
            dept_name = row.get("department") or ""
            dept = None
            if dept_name:
                dept = departments_by_name.get(dept_name.strip().lower())
                if not dept:
                    raise HTTPException(status_code=422, detail=f"Unknown department: {dept_name}.")
                await enforce_department_scope(db, current_user, dept.id)
            level_raw = row.get("user_level") or ""
            if level_raw and not dept:
                raise HTTPException(status_code=422, detail="user_level requires a department.")
            user_level = normalize_user_level(level_raw or "L2")
            await _ensure_user_email_available(db, email)
            provided_password = row.get("password") or ""
            password_generated = False
            if provided_password:
                try:
                    validate_password_strength(provided_password, **policy)
                except ValueError as e:
                    raise HTTPException(status_code=422, detail=str(e))
            else:
                provided_password = secrets.token_urlsafe(24)
                password_generated = True
            new_user = await user_repo.create_user(db, {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "role_id": target_role.id,
                "hashed_password": await get_password_hash_async(provided_password),
                "local_password_enabled": True,
                "must_change_password": True,
                "is_active": True,
            }, commit=False)
            await password_repo.add_password_history(db, new_user.id, new_user.hashed_password, commit=False)
            await _assign_new_user_department(db, new_user, dept.id if dept else None, user_level)
            record_activity(
                db,
                actor_id=current_user.id,
                action="USER_CREATED",
                entity_type="user",
                entity_id=new_user.id,
                description=f"Created user {first_name} {last_name} via bulk import".strip(),
            )
            if target_role.name == "Admin":
                await stage_super_admin_notice(
                    db,
                    actor_id=current_user.id,
                    title="Administrative account created",
                    message=f"{current_user.first_name} {current_user.last_name} created "
                    f"Admin account {email} via bulk import.",
                )
            await db.commit()
            created += 1
            results.append({"row": index, "email": email, "status": "created",
                            "password_generated": password_generated})
        except HTTPException as e:
            await db.rollback()
            results.append({"row": index, "email": email, "status": "error",
                            "error": e.detail if isinstance(e.detail, str) else "Invalid row."})
        except Exception:
            await db.rollback()
            results.append({"row": index, "email": email, "status": "error",
                            "error": "Database transaction failed."})
    return {
        "total_rows": len(rows),
        "created": created,
        "errors": len(rows) - created,
        "results": results,
    }


@router.get('/super-admins', response_model=list[UserResponse], responses={403: {"description": "Forbidden"}})
async def list_super_admins(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, gt=0, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, PERM_USERS_VIEW)
    require_super_admin(current_user)
    stmt = (
        select(User)
        .options(
            selectinload(User.role),
            selectinload(User.position),
            selectinload(User.user_departments).selectinload(UserDepartment.department)
        )
        .join(Role, User.role_id == Role.id)
        .where(User.is_deleted == False, Role.name.in_(list(SUPER_ADMIN_ROLE_NAMES)))
        .order_by(User.first_name, User.last_name)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [serialize_user_obj(user) for user in result.scalars().all()]


@router.get('/super-admins/{user_id}', response_model=UserResponse, responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def get_super_admin(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    require_super_admin(current_user)
    stmt = (
        select(User)
        .options(
            selectinload(User.role),
            selectinload(User.position),
            selectinload(User.user_departments).selectinload(UserDepartment.department)
        )
        .join(Role, User.role_id == Role.id)
        .where(User.id == user_id, User.is_deleted == False, Role.name.in_(list(SUPER_ADMIN_ROLE_NAMES)))
    )
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail=ERR_SUPER_ADMIN_NOT_FOUND)
    return serialize_user_obj(user)


@router.post('/super-admins', response_model=UserResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 409: {"description": "Conflict"}, 500: {"description": "Internal Server Error"}})
async def create_super_admin(
    payload: SuperAdminCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    require_super_admin(current_user)

    if not payload.first_name.strip() or not payload.last_name.strip():
        raise HTTPException(status_code=400, detail='Full name is required.')
    if payload.password != payload.confirm_password:
        raise HTTPException(status_code=400, detail='Passwords do not match.')

    normalized_email = payload.email.lower()
    stmt_check = select(User).where(User.email == normalized_email)
    existing_user = (await db.execute(stmt_check)).scalars().first()
    if existing_user:
        raise HTTPException(status_code=409, detail=ERR_EMAIL_ALREADY_EXISTS)

    try:
        validate_password_strength(payload.password, **(await get_password_policy(db)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    super_admin_role = await get_super_admin_role(db)
    if super_admin_role is None:
        raise HTTPException(status_code=500, detail='Super Admin role is not configured.')

    user_data = {
        'first_name': payload.first_name.strip(),
        'last_name': payload.last_name.strip(),
        'email': normalized_email,
        'hashed_password': await get_password_hash_async(payload.password),
        'local_password_enabled': True,
        'role_id': super_admin_role.id,
        'is_active': payload.is_active,
        'must_change_password': True,
    }

    try:
        created_user = await user_repo.create_user(db, user_data, commit=False)
        await password_repo.add_password_history(
            db,
            created_user.id,
            created_user.hashed_password,
            commit=False,
        )
        audit_entry = await log_audit_trail(
            db,
            current_user.id,
            'CREATE_SUPER_ADMIN',
            'users',
            str(created_user.id),
            None,
            {'email': normalized_email, 'status': 'active' if payload.is_active else 'inactive'},
            commit=False,
        )
        if audit_entry is None:
            raise RuntimeError('Unable to record Super Admin creation audit event.')
        await stage_super_admin_notice(
            db,
            actor_id=current_user.id,
            title="Super Admin account created",
            message=f"{current_user.first_name} {current_user.last_name} created "
            f"Super Admin account {created_user.email}.",
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=ERR_EMAIL_ALREADY_EXISTS,
        ) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail='Super Admin creation failed.') from exc

    return serialize_user_obj(await user_repo.get_user_by_id(db, created_user.id))


@router.put('/super-admins/{user_id}', response_model=UserResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def update_super_admin(
    user_id: UUID,
    payload: SuperAdminUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    require_super_admin(current_user)
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id, User.is_deleted == False)
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail=ERR_SUPER_ADMIN_NOT_FOUND)
    if user.role and user.role.name not in SUPER_ADMIN_ROLE_NAMES:
        raise HTTPException(status_code=403, detail='Only Super Admin accounts can be updated here.')
    if current_user.id == user.id:
        raise HTTPException(status_code=403, detail='You cannot edit your own Super Admin account from this screen.')

    if payload.email and payload.email.lower() != user.email.lower():
        email_check = select(User).where(User.email == payload.email.lower(), User.id != user.id)
        if (await db.execute(email_check)).scalars().first():
            raise HTTPException(status_code=400, detail=ERR_EMAIL_ALREADY_EXISTS)

    old_snapshot = {
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'is_active': user.is_active,
    }
    if payload.first_name is not None:
        user.first_name = payload.first_name.strip() or user.first_name
    if payload.last_name is not None:
        user.last_name = payload.last_name.strip() or user.last_name
    if payload.email is not None:
        user.email = payload.email.lower()
    if payload.is_active is not None:
        user.is_active = payload.is_active

    await db.commit()
    await log_audit_trail(
        db,
        current_user.id,
        'UPDATE_SUPER_ADMIN',
        'users',
        str(user.id),
        old_snapshot,
        {'first_name': user.first_name, 'last_name': user.last_name, 'email': user.email, 'is_active': user.is_active}
    )
    await stage_super_admin_notice(
        db,
        actor_id=current_user.id,
        title="Super Admin account updated",
        message=f"{current_user.first_name} {current_user.last_name} updated "
        f"Super Admin account {user.email}.",
    )
    await db.commit()
    return serialize_user_obj(await user_repo.get_user_by_id(db, user.id))


@router.patch('/super-admins/{user_id}/activate', responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def activate_super_admin(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    require_super_admin(current_user)
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id, User.is_deleted == False)
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail=ERR_SUPER_ADMIN_NOT_FOUND)
    if user.role and user.role.name not in SUPER_ADMIN_ROLE_NAMES:
        raise HTTPException(status_code=403, detail=ERR_NOT_SUPER_ADMIN)
    if current_user.id == user.id:
        raise HTTPException(status_code=403, detail='You cannot activate your own Super Admin account.')

    old_snapshot = {'is_active': user.is_active}
    user.is_active = True
    await db.commit()
    await log_audit_trail(
        db,
        current_user.id,
        'ACTIVATE_SUPER_ADMIN',
        'users',
        str(user.id),
        old_snapshot,
        {'is_active': True}
    )
    await stage_super_admin_notice(
        db,
        actor_id=current_user.id,
        title="Super Admin account activated",
        message=f"{current_user.first_name} {current_user.last_name} activated "
        f"Super Admin account {user.email}.",
    )
    await db.commit()
    return {'message': 'Super Admin activated successfully.'}


@router.patch('/super-admins/{user_id}/deactivate', responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def deactivate_super_admin(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    require_super_admin(current_user)
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id, User.is_deleted == False)
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail=ERR_SUPER_ADMIN_NOT_FOUND)
    if user.role and user.role.name not in SUPER_ADMIN_ROLE_NAMES:
        raise HTTPException(status_code=403, detail=ERR_NOT_SUPER_ADMIN)
    if current_user.id == user.id:
        raise HTTPException(status_code=403, detail='You cannot deactivate your own Super Admin account.')
    if user.is_active and (await count_active_super_admins(db, exclude_user_id=None) <= 2):
        raise HTTPException(status_code=400, detail='Cannot deactivate this Super Admin: at least 2 active Super Admins must remain.')

    old_snapshot = {'is_active': user.is_active}
    user.is_active = False
    await revoke_user_authentication(db, user.id)
    await db.commit()
    await log_audit_trail(
        db,
        current_user.id,
        'DEACTIVATE_SUPER_ADMIN',
        'users',
        str(user.id),
        old_snapshot,
        {'is_active': False}
    )
    await stage_super_admin_notice(
        db,
        actor_id=current_user.id,
        title="Super Admin account deactivated",
        message=f"{current_user.first_name} {current_user.last_name} deactivated "
        f"Super Admin account {user.email}.",
    )
    await db.commit()
    return {'message': 'Super Admin deactivated successfully.'}


@router.delete('/super-admins/{user_id}', responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def delete_super_admin(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    require_super_admin(current_user)
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id, User.is_deleted == False)
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail=ERR_SUPER_ADMIN_NOT_FOUND)
    if user.role and user.role.name not in SUPER_ADMIN_ROLE_NAMES:
        raise HTTPException(status_code=403, detail=ERR_NOT_SUPER_ADMIN)
    if current_user.id == user.id:
        raise HTTPException(status_code=403, detail='You cannot delete your own Super Admin account.')
    if user.is_active and (await count_active_super_admins(db) <= 2):
        raise HTTPException(status_code=400, detail='Cannot delete this Super Admin: at least 2 active Super Admins must remain.')

    old_snapshot = {'email': user.email, 'is_active': user.is_active, 'role': user.role.name if user.role else None}
    if not user.email.startswith('deleted_'):
        user.email = f"deleted_{int(datetime.now().timestamp())}_{user.email}"
    user.is_deleted = True
    user.is_active = False
    await revoke_user_authentication(db, user.id)
    await db.commit()
    await log_audit_trail(
        db,
        current_user.id,
        'DELETE_SUPER_ADMIN',
        'users',
        str(user.id),
        old_snapshot,
        {'deleted': True, 'email': user.email}
    )
    await stage_super_admin_notice(
        db,
        actor_id=current_user.id,
        title="Super Admin account deleted",
        message=f"{current_user.first_name} {current_user.last_name} deleted "
        f"Super Admin account {old_snapshot['email']}.",
    )
    await db.commit()
    return {'message': 'Super Admin deleted successfully.'}


@router.get('/roles/list', responses={403: {"description": "Forbidden"}})
async def get_roles_list(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, PERM_USERS_VIEW)
    stmt = select(Role)
    result = await db.execute(stmt)
    roles = result.scalars().all()
    
    if current_user.role.name != SUPER_ADMIN_ROLE_NAME:
        roles = [r for r in roles if r.name != SUPER_ADMIN_ROLE_NAME]
        
    return [{"id": r.id, "name": r.name} for r in roles]

# 🌟 PERFORMANCE FIX: Added skip and limit for pagination to avoid memory overload
@router.get('/deleted/list', response_model=list[UserResponse], responses={500: {"description": "Internal Server Error"}})
async def get_deleted_users(
    skip: int = Query(0, ge=0), 
    limit: int = Query(100, gt=0, le=500),
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, PERM_USERS_VIEW)
    try:
        stmt = user_repo.get_user_base_query() if hasattr(user_repo, "get_user_base_query") else select(User).options(
            selectinload(User.role),
            selectinload(User.user_departments).selectinload(UserDepartment.department)
        )
        stmt = stmt.where(User.is_deleted == True)
        # D4, mirrored from get_users: non-SA/HR callers see only
        # trashed users from their own departments (deleted
        # memberships excluded on both sides). The SA-accounts
        # filter below is unchanged.
        if not current_user.role or current_user.role.name not in GLOBAL_SCOPE_ROLES:
            stmt = stmt.where(
                User.id.in_(
                    select(UserDepartment.user_id).where(
                        UserDepartment.department_id.in_(
                            select(UserDepartment.department_id).where(
                                UserDepartment.user_id == current_user.id,
                                UserDepartment.is_deleted.is_(False),
                            )
                        ),
                        UserDepartment.is_deleted.is_(False),
                    )
                )
            )
        stmt = stmt.offset(skip).limit(limit)
        result = await db.execute(stmt)
        users = result.scalars().all()
        
        if current_user.role.name != SUPER_ADMIN_ROLE_NAME:
            users = [u for u in users if getattr(u.role, 'name', '') != SUPER_ADMIN_ROLE_NAME]
        return [serialize_user_obj(u) for u in users]
    except Exception as e:
        print("====== DELETED USERS ERROR ======")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

# 🌟 PERFORMANCE FIX: Added skip and limit for pagination to avoid memory overload
@router.get('/', response_model=list[UserResponse], responses={403: {"description": "Forbidden"}})
async def get_users(
    is_active: bool | None = Query(True),
    skip: int = Query(0, ge=0), 
    limit: int = Query(100, gt=0, le=500),
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, PERM_USERS_VIEW)
    stmt = user_repo.get_user_base_query() if hasattr(user_repo, "get_user_base_query") else select(User).options(
        selectinload(User.role),
        selectinload(User.user_departments).selectinload(UserDepartment.department)
    )
    stmt = stmt.where(User.is_deleted == False)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    # D4: the list is department-scoped exactly like the profile
    # view (SA/HR global, everyone else only their own departments).
    # The audit's "unscoped list" premise was wrong — this block
    # already existed; the fix here is only consistency: deleted
    # memberships no longer count for visibility, matching
    # _scope_permission_users in the permission router.
    if not current_user.role or current_user.role.name not in GLOBAL_SCOPE_ROLES:
        stmt = stmt.where(
            User.id.in_(
                select(UserDepartment.user_id).where(
                    UserDepartment.department_id.in_(
                        select(UserDepartment.department_id).where(
                            UserDepartment.user_id == current_user.id,
                            UserDepartment.is_deleted.is_(False),
                        )
                    ),
                    UserDepartment.is_deleted.is_(False),
                )
            )
        )
    stmt = stmt.offset(skip).limit(limit)
    
    result = await db.execute(stmt)
    users = result.scalars().all()

    if current_user.role.name != SUPER_ADMIN_ROLE_NAME:
        users = [u for u in users if getattr(u.role, 'name', '') != SUPER_ADMIN_ROLE_NAME]
    presence_map = await presence_for_users(db, [u.id for u in users])
    return [serialize_user_obj(u, presence_map.get(u.id)) for u in users]

@router.get('/{user_id}', response_model=UserResponse, responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def get_user_profile(user_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, PERM_USERS_VIEW)
    await enforce_user_scope(db, current_user, user_id)
    user = await user_repo.get_user_by_id(db, user_id)
    if not user: raise HTTPException(status_code=404, detail=ERR_USER_NOT_FOUND)

    presence_map = await presence_for_users(db, [user.id])
    return serialize_user_obj(user, presence_map.get(user.id))


async def _validate_user_update_target(
    db: AsyncSession, user: User | None, payload: UserUpdate, current_user: User
) -> User:
    if not user:
        raise HTTPException(status_code=404, detail=ERR_USER_NOT_FOUND)
    if getattr(user, "is_deleted", False):
        raise HTTPException(status_code=400, detail="Cannot edit a deleted account. Please restore the account first.")
    if (
        current_user.role.name != SUPER_ADMIN_ROLE_NAME
        and getattr(user.role, "name", "") == SUPER_ADMIN_ROLE_NAME
    ):
        raise HTTPException(status_code=403, detail="You do not have permission to modify a Super Admin account.")
    await _validate_user_update_role(db, payload)
    await _validate_user_update_email(db, payload, user)
    await _validate_user_update_department(db, payload, current_user)
    # Min-2 SA rule: demoting an active Super Admin must never drop
    # active Super Admins below 2 (a peer SA must exist for MFA
    # reset/disable confirmations).
    if (
        payload.role_id is not None
        and payload.role_id != user.role_id
        and user.is_active
        and getattr(user.role, "name", "") == SUPER_ADMIN_ROLE_NAME
    ):
        target_role = (
            await db.execute(select(Role).where(Role.id == payload.role_id))
        ).scalar_one_or_none()
        if target_role is not None and target_role.name != SUPER_ADMIN_ROLE_NAME:
            remaining = await count_active_super_admins(db, exclude_user_id=user.id)
            if remaining < 2:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot demote this Super Admin: at least 2 active Super Admins must remain.",
                )
    return user


async def _validate_user_update_role(
    db: AsyncSession, payload: UserUpdate
) -> None:
    if not payload.role_id:
        return
    role_result = await db.execute(select(Role).where(Role.id == payload.role_id))
    target_role = role_result.scalar_one_or_none()
    if target_role and target_role.name == SUPER_ADMIN_ROLE_NAME:
        raise HTTPException(status_code=403, detail="You do not have permission to promote a user to Super Admin.")
    if target_role and target_role.name == "Admin":
        raise HTTPException(status_code=403, detail="Only Super Admins can manage administrative accounts.")


async def _validate_user_update_email(
    db: AsyncSession, payload: UserUpdate, user: User
) -> None:
    if not payload.email or payload.email == user.email:
        return
    existing = (await db.execute(select(User).where(User.email == payload.email))).scalars().first()
    if existing:
        raise HTTPException(status_code=400, detail="This email address is already registered to another account (active or deleted).")


async def _validate_user_update_department(
    db: AsyncSession, payload: UserUpdate, current_user: User
) -> None:
    if payload.department_id is None:
        return
    target = await db.execute(
        select(Department).where(
            Department.id == payload.department_id,
            Department.is_deleted.is_(False),
        )
    )
    if target.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Department not found.")
    await enforce_department_scope(db, current_user, payload.department_id)


async def _update_user_department_membership(
    db: AsyncSession,
    user_id: UUID,
    payload: UserUpdate,
    requested_level: str | None,
    existing_ud: UserDepartment | None,
) -> None:
    if not (payload.department_id or requested_level):
        return
    if existing_ud:
        await _update_existing_user_membership(
            db, user_id, payload.department_id, requested_level, existing_ud
        )
        return
    if not payload.department_id:
        raise HTTPException(status_code=400, detail="A department is required before setting user_level.")
    db.add(
        UserDepartment(
            user_id=user_id,
            department_id=payload.department_id,
            user_level=requested_level or "L2",
        )
    )


async def _update_existing_user_membership(
    db: AsyncSession,
    user_id: UUID,
    department_id,
    requested_level: str | None,
    membership: UserDepartment,
) -> None:
    if department_id and str(membership.department_id) != str(department_id):
        old_dept = await db.get(Department, membership.department_id)
        if old_dept and str(old_dept.head_id) == str(user_id):
            old_dept.head_id = None
            db.add(old_dept)
        membership.department_id = department_id
    if requested_level:
        membership.user_level = requested_level
    db.add(membership)


@router.put('/{user_id}', response_model=UserResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 422: {"description": "Unprocessable Entity"}, 500: {"description": "Internal Server Error"}})
async def update_existing_user(user_id: UUID, payload: UserUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, "users.edit")
    await enforce_user_scope(db, current_user, user_id)
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id)
    result = await db.execute(stmt)
    user = await _validate_user_update_target(
        db, result.scalars().first(), payload, current_user
    )

    try:
        requested_level = normalize_user_level(payload.user_level)
        existing_ud = (await db.execute(
            select(UserDepartment).where(
                UserDepartment.user_id == user_id,
                UserDepartment.is_deleted.is_(False),
            )
        )).scalars().first()
        role_changed = payload.role_id is not None and payload.role_id != user.role_id
        level_changed = (
            requested_level is not None
            and existing_ud is not None
            and normalize_user_level(existing_ud.user_level) != requested_level
        )

        update_data = payload.model_dump(
            exclude={"department_id", "user_level"},
            exclude_unset=True,
        )
        for key, value in update_data.items():
            setattr(user, key, value)
        db.add(user)

        # Overrides are explicit grants/denials for the previous baseline.
        # Flush them in the same transaction as the role/level change so the
        # next request can only resolve against the new baseline.
        if role_changed or level_changed:
            await permission_repo.purge_user_overrides(db, user_id)

        await _update_user_department_membership(
            db, user_id, payload, requested_level, existing_ud
        )
        record_activity(
            db,
            actor_id=current_user.id,
            action="USER_UPDATED",
            entity_type="user",
            entity_id=user.id,
            description=f"Updated user {user.first_name} {user.last_name}".strip(),
        )
        await db.commit()
        await bump_user_permissions_version(user_id)
        updated_user = await user_repo.get_user_by_id(db, user.id)
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database update failed: {str(e)}")
        
    db.expunge(updated_user)
    refreshed_user = await user_repo.get_user_by_id(db, updated_user.id)
    return serialize_user_obj(refreshed_user)

@router.post('/{user_id}/offboard', responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def offboard_user(user_id: UUID, payload: UserOffboardRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Formal departure flow (retention design, 2026-10-23).

    RESIGNED / TERMINATED: lifecycle status + leaving date are set,
    login dies at once (sessions/OAuth revoked, account inactive),
    the account moves to the soft-deleted state, the leaving date is
    stamped onto the person's KPI records and tasks (the "Resigned ·
    <date>" history label), any report-download grants end, and the
    departure is audit-logged with its reason. ALL business data is
    kept. SUSPENDED only pauses the account.
    """
    enforce_permission(current_user, "users.delete")
    await enforce_user_scope(db, current_user, user_id)
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id)
    user = (await db.execute(stmt)).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail='User not found in the database.')
    if getattr(user, 'is_deleted', False):
        raise HTTPException(status_code=400, detail='This account is already offboarded or deleted.')
    ensure_user_deletion_allowed(user, current_user)

    from app.core.timezone import APPLICATION_TIMEZONE

    new_status = UserStatus(payload.status)
    user.status = new_status.value
    if new_status == UserStatus.SUSPENDED:
        user.is_active = False
        await revoke_user_authentication(db, user.id)
        db.add(AuditLog(
            user_id=current_user.id, action="USER_SUSPENDED",
            table_name="users", record_id=str(user.id),
            new_value={"reason": payload.reason},
        ))
        await db.commit()
        return {'message': 'User suspended: login is disabled and the account is paused. No data was changed.'}

    user.resigned_at = payload.resigned_at or datetime.now(APPLICATION_TIMEZONE).date()
    # Same email handling as soft delete so Restore can bring the
    # original address back (and a new account may reuse it meanwhile).
    if not user.email.startswith("deleted_"):
        user.email = f"deleted_{int(datetime.now().timestamp())}_{user.email}"
    await _detach_department_head(db, user_id)
    await revoke_user_authentication(db, user.id)
    await user_repo.soft_delete_user(db, user)
    # Stamp the leaving date + name onto kept history rows, and end
    # any report-download grants the person held (a grant must never
    # survive the account leaving).
    await retention.stamp_resigned_snapshots(db, user)
    from sqlalchemy import update as _sa_update

    from app.models.report_download import ReportDownloadGrant

    await db.execute(
        _sa_update(ReportDownloadGrant)
        .where(
            ReportDownloadGrant.user_id == user_id,
            ReportDownloadGrant.is_active.is_(True),
        )
        .values(
            is_active=False,
            revoked_at=datetime.now(UTC).replace(tzinfo=None),
            revoked_by=current_user.id,
        )
    )
    db.add(AuditLog(
        user_id=current_user.id, action="USER_OFFBOARDED",
        table_name="users", record_id=str(user.id),
        new_value={
            "status": new_status.value,
            "resigned_at": user.resigned_at.isoformat(),
            "reason": payload.reason,
            "employee": retention.display_name(user),
        },
    ))
    await db.commit()
    return {'message': 'Employee offboarded: login is disabled, the account is in the trash, and all KPI records and tasks are kept.'}

@router.delete('/{user_id}', responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 400: {"description": "Bad Request"}})
async def delete_existing_user(user_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, "users.delete")
    await enforce_user_scope(db, current_user, user_id)
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(status_code=404, detail='User not found in the database.')
        
    if getattr(user, 'is_deleted', False):
        return {'message': 'User is already soft deleted.'}
        
    ensure_user_deletion_allowed(user, current_user)

    # 🌟 SECURITY FIX: Email ကို Timestamp တပ်၍ ပြောင်းလဲလိုက်မည် (Unique Constraint မငြိစေရန်)
    if not user.email.startswith("deleted_"):
        user.email = f"deleted_{int(datetime.now().timestamp())}_{user.email}"
        
    await _detach_department_head(db, user_id)
    await revoke_user_authentication(db, user.id)
    await user_repo.soft_delete_user(db, user)
    record_activity(
        db,
        actor_id=current_user.id,
        action="USER_DELETED",
        entity_type="user",
        entity_id=user.id,
        description=f"Deactivated user {user.first_name} {user.last_name}".strip(),
    )
    await db.commit()
    return {'message': 'User disabled and removed from department successfully (Soft Delete).'}

@router.delete('/{user_id}/permanent', responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}, 400: {"description": "Bad Request"}})
async def permanent_delete_user(user_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Hard-delete an account row — and ONLY the account row.

    Retention scope (Zenn's final decisions, 2026-10-23): what
    matters is the KPI record history, so kpi_records + kpi_history
    are kept forever, and kept tasks stay at snapshot level (title,
    owner name snapshot, status/period) while their detail tables go:
    task_evidence (rows AND the stored files — no orphans),
    task_histories, and reviews on the person's own tasks. Detached
    and kept (SET NULL): action plans, assignments, targets the
    person created, delegations, submission batches, approval queue
    entries, and reviews the person wrote on other people's tasks.
    Removed with the account: operational data and the person's
    daily activity timeline ("nobody looks back at daily activity").
    The models already carry these FK actions; the production
    migration DDL alignment is tracked separately with Database.

    Gates for a departed employee: their leaving year must have
    ended, the company-wide full-year report for that year must have
    been downloaded, and the year's backup must be confirmed. An
    active employee with any KPI records or tasks must be offboarded
    first; an account with no history at all (a test account) may be
    removed directly. Before anything is deleted, the departed
    identity is captured into departed_identities and linked from
    the kept rows (grouping + Relink key).
    """
    from sqlalchemy import delete as sa_delete
    from sqlalchemy import update as sa_update

    from app.models.audit_log import AuditLog as AuditRow
    from app.modules.auth.models import AuthSession as AuthSessionRow
    from app.modules.auth.models import LoginHistory as LoginHistoryRow
    from app.modules.auth.models import PasswordHistory as PasswordRow
    from app.modules.auth.models import UnlockRequest as UnlockRow
    from app.modules.departments.models import Department as DepartmentRow
    from app.modules.departments.models import UserDepartment as MembershipRow
    from app.modules.kpi.models import ActionPlan as ActionPlanRow
    from app.modules.kpi.models import KPIAssignment as AssignmentRow
    from app.modules.kpi.models import KPIFormula as FormulaRow
    from app.modules.kpi.models import KPIRecord as RecordRow
    from app.modules.notifications.models import Notification as NotificationRow
    from app.modules.targets.models import Target as TargetRow
    from app.modules.tasks.models.activity_timeline import ActivityTimeline as ActivityRow
    from app.modules.tasks.models.approval_queue import ApprovalQueue as ApprovalQueueRow
    from app.modules.tasks.models.submission_batch import SubmissionBatch as SubmissionBatchRow
    from app.modules.tasks.models.task import Task as TaskRow
    from app.modules.tasks.models.task_evidence import TaskEvidence as TaskEvidenceRow
    from app.modules.tasks.models.task_history import TaskHistory as TaskHistoryRow
    from app.modules.tasks.models.task_review import TaskReview as TaskReviewRow
    from app.modules.users.models import OAuthAccount as OAuthRow
    from app.modules.users.models import (
        UserPermissionOverride as OverrideRow,
    )

    enforce_permission(current_user, "users.delete")
    if current_user.role.name not in [SUPER_ADMIN_ROLE_NAME]:
        raise HTTPException(status_code=403, detail='Only Super Admins can permanently delete accounts.')
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id)
    user = (await db.execute(stmt)).scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail=ERR_USER_NOT_FOUND)
    ensure_user_deletion_allowed(user, current_user)


    async def _count(model, column) -> int:
        return int(
            (await db.execute(select(func.count(model.id)).where(column == user_id))).scalar_one()
        )

    record_count = await _count(RecordRow, RecordRow.user_id)
    task_count = await _count(TaskRow, TaskRow.owner_id)
    departed = user.status in (UserStatus.RESIGNED.value, UserStatus.TERMINATED.value) or user.resigned_at is not None
    if departed:
        from app.core.timezone import APPLICATION_TIMEZONE

        await retention.ensure_hard_delete_unlocked(
            db, user=user, today=datetime.now(APPLICATION_TIMEZONE).date()
        )
    elif record_count or task_count:
        raise HTTPException(
            status_code=400,
            detail='This employee still has KPI records or tasks. Offboard them first — permanent deletion only unlocks after their leaving year ends.',
        )

    try:
        # 1. Identity capture FIRST: backstop the resigned snapshots,
        #    then create/link the departed-identity registry row.
        await retention.stamp_resigned_snapshots(db, user)
        identity = await retention.capture_departed_identity(db, user)

        # 2. Kill authentication paths + detach the references the
        #    decided scope leaves to us.
        await revoke_user_authentication(db, user.id)
        # Every detach goes through ORM updates (see
        # _detach_department_head for why raw text() is banned here).
        for model, column in (
            (DepartmentRow, DepartmentRow.head_id),
            (User, User.delegated_to_id),
            (TaskRow, TaskRow.assigner_id),
            (FormulaRow, FormulaRow.created_by_id),
            (ActionPlanRow, ActionPlanRow.reviewed_by_id),
            (TargetRow, TargetRow.created_by_id),
            (ActionPlanRow, ActionPlanRow.user_id),
            (AssignmentRow, AssignmentRow.user_id),
            (SubmissionBatchRow, SubmissionBatchRow.submitter_id),
            (ApprovalQueueRow, ApprovalQueueRow.reviewer_id),
            (TaskReviewRow, TaskReviewRow.reviewer_id),
        ):
            await db.execute(
                sa_update(model).where(column == user_id).values({column.key: None})
            )

        # 3. Task detail purge: kept tasks stay at snapshot level.
        #    FIRST the outcome facts are copied onto the task rows
        #    (what was finished, when, with what result — the
        #    handover record), THEN the trail goes: evidence rows +
        #    their stored files, histories, reviews on the person's
        #    own tasks, and the person's activity timeline. File
        #    paths are collected first; the files themselves are
        #    deleted after the commit.
        own_task_ids = list(
            (await db.execute(select(TaskRow.id).where(TaskRow.owner_id == user_id))).scalars().all()
        )
        await retention.stamp_task_outcomes(db, own_task_ids)
        evidence_filter = or_(
            TaskEvidenceRow.task_id.in_(own_task_ids),
            TaskEvidenceRow.uploaded_by == user_id,
        )
        evidence_paths = list(
            (await db.execute(select(TaskEvidenceRow.file_path).where(evidence_filter))).scalars().all()
        )
        await db.execute(sa_delete(TaskEvidenceRow).where(evidence_filter))
        await db.execute(
            sa_delete(TaskHistoryRow).where(
                or_(
                    TaskHistoryRow.task_id.in_(own_task_ids),
                    TaskHistoryRow.changed_by == user_id,
                )
            )
        )
        if own_task_ids:
            await db.execute(
                sa_delete(TaskReviewRow).where(TaskReviewRow.task_id.in_(own_task_ids))
            )
        await db.execute(sa_delete(ActivityRow).where(ActivityRow.user_id == user_id))

        # 4. Account/operational data dies with the account.
        for model in (
            AuthSessionRow, OAuthRow, PasswordRow, LoginHistoryRow,
            NotificationRow, MembershipRow, OverrideRow, UnlockRow,
        ):
            await db.execute(sa_delete(model).where(model.user_id == user_id))

        # 5. Audit BEFORE the row goes (the actor survives).
        db.add(AuditLog(
            user_id=current_user.id, action="USER_HARD_DELETED",
            table_name="users", record_id=str(user.id),
            new_value={
                "employee": retention.display_name(user),
                "resigned_at": user.resigned_at.isoformat() if user.resigned_at else None,
                "identity_id": str(identity.id) if identity else None,
                "kpi_records_kept": record_count,
                "tasks_kept": task_count,
                "evidence_files_removed": len(evidence_paths),
            },
        ))

        # 6. Detach kept history explicitly (the schema's SET NULL
        #    does the same on PostgreSQL), then the account row.
        await db.execute(
            sa_update(RecordRow).where(RecordRow.user_id == user_id).values(user_id=None)
        )
        await db.execute(
            sa_update(TaskRow).where(TaskRow.owner_id == user_id).values(owner_id=None)
        )
        await db.execute(
            sa_update(AuditRow).where(AuditRow.user_id == user_id).values(user_id=None)
        )
        await db.delete(user)
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        logger.exception("Permanent user deletion failed")
        raise HTTPException(status_code=500, detail="Permanent deletion failed. Nothing was deleted.")

    # 7. Stored evidence files last (post-commit, best-effort): a
    #    storage failure is logged loudly, never silently orphaned.
    if evidence_paths:
        from app.services.storage_service import storage_service

        for path in evidence_paths:
            try:
                await storage_service.delete_file(path)
            except Exception:
                logger.exception("Failed to delete evidence file %s after hard delete", path)

    return {'message': 'Account permanently deleted. KPI records, tasks and audit history were kept.'}

@router.put('/{user_id}/reset-password', responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def reset_user_password(user_id: UUID, payload: PasswordResetAdmin, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, "users.password_reset")
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id)
    await enforce_user_scope(db, current_user, user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(status_code=404, detail=ERR_USER_NOT_FOUND)
        
    if getattr(user, 'is_deleted', False):
        raise HTTPException(status_code=400, detail='Cannot reset password for a deleted account. Please restore the account first.')
        
    if current_user.role.name != SUPER_ADMIN_ROLE_NAME and getattr(user.role, 'name', '') == SUPER_ADMIN_ROLE_NAME:
        raise HTTPException(status_code=403, detail='You do not have permission to reset a Super Admin account password.')
        
    if (
        payload.confirm_new_password is not None
        and payload.new_password != payload.confirm_new_password
    ):
        raise HTTPException(status_code=400, detail='New password confirmation does not match.')
    try:
        validate_password_strength(payload.new_password, **(await get_password_policy(db)))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    is_valid_history = await password_repo.check_password_history(db, user_id, payload.new_password)
    if not is_valid_history:
        raise HTTPException(status_code=400, detail='Cannot reuse any of the last 5 passwords.')
        
    hashed_pass = await get_password_hash_async(payload.new_password)
    await user_repo.update_user(db, user, {
        'hashed_password': hashed_pass,
        'local_password_enabled': True,
        'must_change_password': True,
    })
    await revoke_user_authentication(db, user.id)
    await password_repo.add_password_history(db, user.id, hashed_pass)
    
    return {'message': 'Password has been reset successfully.'}

@router.post('/{user_id}/restore', response_model=UserResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def restore_user(user_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, "users.edit")
    if current_user.role.name not in [SUPER_ADMIN_ROLE_NAME]:
        raise HTTPException(status_code=403, detail='Only Super Admins can restore accounts.')
        
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user: raise HTTPException(status_code=404, detail=ERR_USER_NOT_FOUND)
    if not getattr(user, 'is_deleted', False): raise HTTPException(status_code=400, detail='This user account is not deleted.')
        
    # 🌟 SECURITY FIX: ပြန်လည်အသက်သွင်းပါက မူလ Email ကို ပြန်ထားပေးမည်၊ အခြားသူ ယူထားပြီးပါက တားဆီးမည်
    if user.email.startswith("deleted_"):
        parts = user.email.split("_", 2)
        if len(parts) == 3:
            original_email = parts[2]
            email_check = await db.execute(select(User).where(User.email == original_email))
            if email_check.scalars().first():
                raise HTTPException(status_code=400, detail='Cannot restore: Original email is currently registered to another active account.')
            user.email = original_email

    # Re-hire case 1 (retention design): restoring a departed
    # employee brings the lifecycle back to ACTIVE and clears the
    # leaving date; their KPI records and tasks reconnect through
    # the account automatically. The departure itself stays in the
    # audit trail.
    was_departed = user.status in (UserStatus.RESIGNED.value, UserStatus.TERMINATED.value)
    user.is_deleted = False
    user.is_active = True
    user.status = UserStatus.ACTIVE.value
    user.resigned_at = None
    db.add(user)
    db.add(AuditLog(
        user_id=current_user.id,
        action="USER_REHIRED" if was_departed else "USER_RESTORED",
        table_name="users", record_id=str(user.id),
        new_value={"employee": retention.display_name(user)},
    ))
    await db.commit()

    restored_user = await user_repo.get_user_by_id(db, user_id)
    return serialize_user_obj(restored_user)

@router.post('/{user_id}/set-leave', responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def set_user_leave(user_id: UUID, payload: UserLeaveRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.id != user_id and current_user.role.name not in [SUPER_ADMIN_ROLE_NAME, 'Admin']:
        raise HTTPException(status_code=403, detail='You do not have permission to set leave for this user.')
        
    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalars().first()
    if not user: raise HTTPException(status_code=404, detail=ERR_USER_NOT_FOUND)
    
    user.is_on_leave = payload.is_on_leave
    if user.is_on_leave:
        if not payload.leave_start_date or not payload.leave_end_date:
            raise HTTPException(status_code=400, detail="Start and End dates are required when setting leave.")
        user.leave_start_date = payload.leave_start_date.replace(tzinfo=None)
        user.leave_end_date = payload.leave_end_date.replace(tzinfo=None)
        user.delegated_to_id = payload.delegated_to_id
    else:
        user.leave_start_date = None
        user.leave_end_date = None
        user.delegated_to_id = None
        
    db.add(user)
    await db.commit()
    return {"message": "Leave and delegation settings have been updated successfully."}

# ==============================================================================
# SUPER ADMIN: Individual User Process Tracking API
# ==============================================================================
@router.get("/{user_id}/process-tracking", tags=["Super Admin Tracking"], responses={404: {"description": "Not Found"}})
async def get_user_process_tracking(
    user_id: UUID, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    user_check = await db.get(User, user_id)
    if not user_check:
        raise HTTPException(status_code=404, detail=ERR_USER_NOT_FOUND)

    enforce_permission(current_user, PERM_USERS_VIEW)
    await enforce_user_scope(db, current_user, user_id)
    metrics_stmt = (
        select(Task.status, func.count(Task.id))
        .where(Task.owner_id == user_id)
        .where(Task.is_deleted == False)
        .group_by(Task.status)
    )
    metrics_req = await db.execute(metrics_stmt)
    
    process_metrics = {
        "DRAFT": 0, "PENDING_L1_REVIEW": 0, "PENDING_L0_REVIEW": 0, "COMPLETED": 0, "REJECTED": 0
    }
    
    for status_enum, count in metrics_req.all():
        status_val = status_enum.value if hasattr(status_enum, 'value') else str(status_enum)
        if "Draft" in status_val: process_metrics["DRAFT"] = count
        elif "L1" in status_val: process_metrics["PENDING_L1_REVIEW"] = count
        elif "L0" in status_val: process_metrics["PENDING_L0_REVIEW"] = count
        elif "Completed" in status_val or "Approved" in status_val: process_metrics["COMPLETED"] += count
        elif "Rejected" in status_val: process_metrics["REJECTED"] = count

    timeline_stmt = (
        select(TaskHistory, Task.title, User.first_name, User.last_name)
        .join(Task, TaskHistory.task_id == Task.id)
        .outerjoin(User, TaskHistory.changed_by == User.id)
        .where(Task.owner_id == user_id, Task.is_deleted == False)
        .order_by(TaskHistory.created_at.desc())
        .limit(15)
    )
    timeline_req = await db.execute(timeline_stmt)

    recent_activities = []
    for history, task_title, f_name, l_name in timeline_req.all():
        actor_name = f"{f_name} {l_name}".strip() if f_name else "System"
        recent_activities.append({
            "task_title": task_title,
            "action_by": actor_name,
            "action": history.action,
            "remarks": history.remarks,
            "timestamp": history.created_at.isoformat()
        })

    return {
        "user_name": f"{user_check.first_name} {user_check.last_name}".strip(),
        "tracking_metrics": process_metrics,
        "recent_process_activities": recent_activities,
        "progress_trend": await _get_process_trend(db, [user_id]),
    }
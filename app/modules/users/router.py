import traceback
from fastapi import APIRouter, Depends, HTTPException, status, Query # 🌟 PERFORMANCE FIX: Added Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import text
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from typing import List
from uuid import UUID
from datetime import datetime

from app.database.connection import get_db
from app.modules.users.schemas import UserCreate, UserUpdate, UserResponse, RoleResponse, UserDeptResponse, UserLeaveRequest, SuperAdminCreate, SuperAdminUpdate
from app.modules.users.password_schemas import PasswordResetAdmin
from app.modules.users import repo as user_repo
from app.modules.users import password_repo
from app.modules.users import permission_repo

from app.core.dependencies import RoleChecker, get_current_user, enforce_permission
from app.utils.audit import log_audit_trail
from app.services.activity_service import record_activity
from app.core.security import get_password_hash
from app.core.password_policy import get_password_policy, validate_password_strength

# 🌟 BUG FIX: Role Model ကို models.py မှ တိုက်ရိုက်ယူပါမည် (roles.py အဟောင်းကို မသုံးပါ)
from app.modules.users.models import User, Role
from app.modules.departments.models import Department, UserDepartment
from app.modules.tasks.models.task import Task
from app.modules.tasks.models.task_history import TaskHistory

router = APIRouter()
admin_checker = RoleChecker(['Super Admin', 'Admin'])
SUPER_ADMIN_ROLE_NAMES = {'Super Admin'}
VALID_USER_LEVELS = {'L0', 'L1', 'L2'}


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


async def get_super_admin_role(db: AsyncSession):
    role_stmt = select(Role).where(Role.name == 'Super Admin')
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


def serialize_user_obj(u: User) -> UserResponse:
    role_data = RoleResponse(id=u.role.id, name=u.role.name) if u.role else None
    
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
        last_login=getattr(u, 'last_login', None)
    )

@router.post('/', response_model=UserResponse) 
async def create_new_user(payload: UserCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(admin_checker)):
    enforce_permission(current_user, "users.create")
    if current_user.role.name != 'Super Admin':
        role_req = await db.execute(select(Role).where(Role.id == payload.role_id))
        target_role = role_req.scalar_one_or_none()
        if target_role and target_role.name == 'Super Admin':
            raise HTTPException(status_code=403, detail='You do not have permission to create a Super Admin account.')

    # 🌟 FIX 1: Smart Soft-Delete Recovery (Ghost Account Prevention)
    stmt_check = select(User).where(User.email == payload.email)
    existing_any = (await db.execute(stmt_check)).scalars().first()
    
    if existing_any:
        if getattr(existing_any, 'is_deleted', False):
            raise HTTPException(
                status_code=400, 
                detail=f"အကောင့်ဖန်တီး၍မရပါ။ '{payload.email}' သည် ဖျက်သိမ်းထားသော အကောင့်ဟောင်းတစ်ခုဖြစ်သည်။ ကျေးဇူးပြု၍ အသစ်မဖန်တီးဘဲ အကောင့်ဟောင်းကိုသာ Trash (Deleted List) မှတဆင့် Restore (ပြန်လည်အသက်သွင်း) ပေးပါ။"
            )
        else:
            raise HTTPException(status_code=400, detail='Email address is already registered and currently active.')
            
    user_data = payload.model_dump(exclude={"department_id", "user_level", "password", "temporary_password", "temp_password"}, exclude_unset=True)
    
    provided_password = (
        getattr(payload, 'password', None) or 
        getattr(payload, 'temporary_password', None) or 
        getattr(payload, 'temp_password', None) or
        (payload.model_dump().get('password')) or
        (payload.model_dump().get('temporary_password'))
    )
    
    if not provided_password:
        raise HTTPException(status_code=400, detail='Password (Temporary Password) is required.')
        
    dept_id = getattr(payload, 'department_id', None)
    user_level = normalize_user_level(getattr(payload, 'user_level', 'L2') or 'L2')
    
    try:
        validate_password_strength(provided_password, **(await get_password_policy(db)))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    user_data['hashed_password'] = get_password_hash(provided_password)
    user_data['must_change_password'] = True 
    
    try:
        new_user = await user_repo.create_user(db, user_data, commit=False)
        await password_repo.add_password_history(
            db, new_user.id, new_user.hashed_password, commit=False
        )
        
        if dept_id:
            dept_check = await db.execute(select(Department).where(Department.id == dept_id, Department.is_deleted == False))
            target_dept = dept_check.scalar_one_or_none()
            
            if not target_dept:
                raise HTTPException(status_code=404, detail='Department not found.')

            new_ud = UserDepartment(
                user_id=new_user.id,
                department_id=dept_id,
                user_level=user_level
            )
            db.add(new_ud)

            if user_level == "L0":
                target_dept.head_id = new_user.id
                db.add(target_dept)
        await record_activity(
            db,
            actor_id=current_user.id,
            action="USER_CREATED",
            entity_type="user",
            entity_id=new_user.id,
            description=f"Created user {new_user.first_name} {new_user.last_name}".strip(),
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


@router.get('/super-admins', response_model=List[UserResponse])
async def list_super_admins(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, gt=0, le=500),
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
        .where(User.is_deleted == False, Role.name.in_(list(SUPER_ADMIN_ROLE_NAMES)))
        .order_by(User.first_name, User.last_name)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [serialize_user_obj(user) for user in result.scalars().all()]


@router.get('/super-admins/{user_id}', response_model=UserResponse)
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
        raise HTTPException(status_code=404, detail='Super Admin not found.')
    return serialize_user_obj(user)


@router.post('/super-admins', response_model=UserResponse)
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
        raise HTTPException(status_code=409, detail='An account with this email already exists.')

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
        'hashed_password': get_password_hash(payload.password),
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
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail='An account with this email already exists.',
        ) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail='Super Admin creation failed.') from exc

    return serialize_user_obj(await user_repo.get_user_by_id(db, created_user.id))


@router.put('/super-admins/{user_id}', response_model=UserResponse)
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
        raise HTTPException(status_code=404, detail='Super Admin not found.')
    if user.role and user.role.name not in SUPER_ADMIN_ROLE_NAMES:
        raise HTTPException(status_code=403, detail='Only Super Admin accounts can be updated here.')
    if current_user.id == user.id:
        raise HTTPException(status_code=403, detail='You cannot edit your own Super Admin account from this screen.')

    if payload.email and payload.email.lower() != user.email.lower():
        email_check = select(User).where(User.email == payload.email.lower(), User.id != user.id)
        if (await db.execute(email_check)).scalars().first():
            raise HTTPException(status_code=400, detail='An account with this email already exists.')

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
    return serialize_user_obj(await user_repo.get_user_by_id(db, user.id))


@router.patch('/super-admins/{user_id}/activate')
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
        raise HTTPException(status_code=404, detail='Super Admin not found.')
    if user.role and user.role.name not in SUPER_ADMIN_ROLE_NAMES:
        raise HTTPException(status_code=403, detail='This user is not a Super Admin account.')
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
    return {'message': 'Super Admin activated successfully.'}


@router.patch('/super-admins/{user_id}/deactivate')
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
        raise HTTPException(status_code=404, detail='Super Admin not found.')
    if user.role and user.role.name not in SUPER_ADMIN_ROLE_NAMES:
        raise HTTPException(status_code=403, detail='This user is not a Super Admin account.')
    if current_user.id == user.id:
        raise HTTPException(status_code=403, detail='You cannot deactivate your own Super Admin account.')
    if user.is_active and (await count_active_super_admins(db, exclude_user_id=None) <= 1):
        raise HTTPException(status_code=400, detail='Cannot deactivate the last active Super Admin.')

    old_snapshot = {'is_active': user.is_active}
    user.is_active = False
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
    return {'message': 'Super Admin deactivated successfully.'}


@router.delete('/super-admins/{user_id}')
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
        raise HTTPException(status_code=404, detail='Super Admin not found.')
    if user.role and user.role.name not in SUPER_ADMIN_ROLE_NAMES:
        raise HTTPException(status_code=403, detail='This user is not a Super Admin account.')
    if current_user.id == user.id:
        raise HTTPException(status_code=403, detail='You cannot delete your own Super Admin account.')
    if user.is_active and (await count_active_super_admins(db) <= 1):
        raise HTTPException(status_code=400, detail='Cannot delete the last active Super Admin.')

    old_snapshot = {'email': user.email, 'is_active': user.is_active, 'role': user.role.name if user.role else None}
    if not user.email.startswith('deleted_'):
        user.email = f"deleted_{int(datetime.now().timestamp())}_{user.email}"
    user.is_deleted = True
    user.is_active = False
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
    return {'message': 'Super Admin deleted successfully.'}


@router.get('/roles/list')
async def get_roles_list(db: AsyncSession = Depends(get_db), current_user: User = Depends(admin_checker)):
    stmt = select(Role)
    result = await db.execute(stmt)
    roles = result.scalars().all()
    
    if current_user.role.name != 'Super Admin':
        roles = [r for r in roles if r.name != 'Super Admin']
        
    return [{"id": r.id, "name": r.name} for r in roles]

# 🌟 PERFORMANCE FIX: Added skip and limit for pagination to avoid memory overload
@router.get('/deleted/list', response_model=List[UserResponse])
async def get_deleted_users(
    skip: int = Query(0, ge=0), 
    limit: int = Query(100, gt=0, le=500),
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(admin_checker)
):
    try:
        stmt = user_repo.get_user_base_query() if hasattr(user_repo, "get_user_base_query") else select(User).options(
            selectinload(User.role),
            selectinload(User.user_departments).selectinload(UserDepartment.department)
        )
        stmt = stmt.where(User.is_deleted == True).offset(skip).limit(limit)
        
        result = await db.execute(stmt)
        users = result.scalars().all()
        
        if current_user.role.name != 'Super Admin':
            users = [u for u in users if getattr(u.role, 'name', '') != 'Super Admin']
        return [serialize_user_obj(u) for u in users]
    except Exception as e:
        print("====== DELETED USERS ERROR ======")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

# 🌟 PERFORMANCE FIX: Added skip and limit for pagination to avoid memory overload
@router.get('/', response_model=List[UserResponse])
async def get_users(
    is_active: bool | None = Query(True),
    skip: int = Query(0, ge=0), 
    limit: int = Query(100, gt=0, le=500),
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(admin_checker)
):
    enforce_permission(current_user, "users.view")
    stmt = user_repo.get_user_base_query() if hasattr(user_repo, "get_user_base_query") else select(User).options(
        selectinload(User.role),
        selectinload(User.user_departments).selectinload(UserDepartment.department)
    )
    stmt = stmt.where(User.is_deleted == False)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    stmt = stmt.offset(skip).limit(limit)
    
    result = await db.execute(stmt)
    users = result.scalars().all()
    
    if current_user.role.name != 'Super Admin':
        users = [u for u in users if getattr(u.role, 'name', '') != 'Super Admin']
    return [serialize_user_obj(u) for u in users]

@router.get('/{user_id}', response_model=UserResponse)
async def get_user_profile(user_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, "users.view")
    user = await user_repo.get_user_by_id(db, user_id)
    if not user: raise HTTPException(status_code=404, detail='User not found.')
    
    return serialize_user_obj(user)

@router.put('/{user_id}', response_model=UserResponse)
async def update_existing_user(user_id: UUID, payload: UserUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(admin_checker)):
    enforce_permission(current_user, "users.edit")
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(status_code=404, detail='User not found.')
        
    if getattr(user, 'is_deleted', False):
        raise HTTPException(status_code=400, detail='Cannot edit a deleted account. Please restore the account first.')
        
    if current_user.role.name != 'Super Admin':
        if getattr(user.role, 'name', '') == 'Super Admin':
            raise HTTPException(status_code=403, detail='You do not have permission to modify a Super Admin account.')
            
    if payload.role_id:
        role_req = await db.execute(select(Role).where(Role.id == payload.role_id))
        target_role = role_req.scalar_one_or_none()
        if target_role and target_role.name == 'Super Admin':
            raise HTTPException(status_code=403, detail='You do not have permission to promote a user to Super Admin.')

    # 🌟 FIX 2: Email Update Conflict Prevention
    if payload.email and payload.email != user.email:
        stmt_email_check = select(User).where(User.email == payload.email)
        existing_email_user = (await db.execute(stmt_email_check)).scalars().first()
        if existing_email_user:
            raise HTTPException(status_code=400, detail="This email address is already registered to another account (active or deleted).")

    try:
        requested_level = normalize_user_level(payload.user_level)
        existing_ud = (await db.execute(
            select(UserDepartment).where(UserDepartment.user_id == user_id)
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

        if payload.department_id or requested_level:
            ud_record = existing_ud
            
            if ud_record:
                if payload.department_id and str(ud_record.department_id) != str(payload.department_id):
                    old_dept_req = await db.execute(select(Department).where(Department.id == ud_record.department_id))
                    old_dept = old_dept_req.scalar_one_or_none()
                    if old_dept and str(old_dept.head_id) == str(user_id):
                        old_dept.head_id = None
                        db.add(old_dept)
                        
                    ud_record.department_id = payload.department_id
                if requested_level:
                    ud_record.user_level = requested_level
                db.add(ud_record)
            else:
                if not payload.department_id:
                    raise HTTPException(status_code=400, detail='A department is required before setting user_level.')
                new_ud = UserDepartment(
                    user_id=user_id,
                    department_id=payload.department_id,
                    user_level=requested_level or "L2",
                )
                db.add(new_ud)
        await record_activity(
            db,
            actor_id=current_user.id,
            action="USER_UPDATED",
            entity_type="user",
            entity_id=user.id,
            description=f"Updated user {user.first_name} {user.last_name}".strip(),
        )
        await db.commit()
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

@router.delete('/{user_id}')
async def delete_existing_user(user_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(admin_checker)):
    enforce_permission(current_user, "users.delete")
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(status_code=404, detail='User not found in the database.')
        
    if getattr(user, 'is_deleted', False):
        return {'message': 'User is already soft deleted.'}
        
    if current_user.role.name != 'Super Admin' and getattr(user.role, 'name', '') == 'Super Admin':
        raise HTTPException(status_code=403, detail='You do not have permission to delete a Super Admin account.')

    # 🌟 SECURITY FIX: Email ကို Timestamp တပ်၍ ပြောင်းလဲလိုက်မည် (Unique Constraint မငြိစေရန်)
    if not user.email.startswith("deleted_"):
        user.email = f"deleted_{int(datetime.now().timestamp())}_{user.email}"
        
    await db.execute(text("UPDATE departments SET head_id = NULL WHERE head_id = :uid"), {"uid": str(user_id)})
    await user_repo.soft_delete_user(db, user)
    await record_activity(
        db,
        actor_id=current_user.id,
        action="USER_DELETED",
        entity_type="user",
        entity_id=user.id,
        description=f"Deactivated user {user.first_name} {user.last_name}".strip(),
    )
    await db.commit()
    return {'message': 'User disabled and removed from department successfully (Soft Delete).'}

@router.delete('/{user_id}/permanent')
async def permanent_delete_user(user_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, "users.delete")
    if current_user.role.name not in ['Super Admin']:
        raise HTTPException(status_code=403, detail='Only Super Admins can permanently delete accounts.')
        
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(status_code=404, detail='User not found.')
        
    try:
        uid_param = {"uid": str(user_id)}
        
        await db.execute(text("UPDATE departments SET head_id = NULL WHERE head_id = :uid"), uid_param)
        await db.execute(text("UPDATE tasks SET assigner_id = NULL WHERE assigner_id = :uid"), uid_param)
        await db.execute(text("UPDATE kpi_formulas SET created_by_id = NULL WHERE created_by_id = :uid"), uid_param)
        await db.execute(text("UPDATE kpi_action_plans SET reviewed_by_id = NULL WHERE reviewed_by_id = :uid"), uid_param)
        
        await db.execute(text("DELETE FROM activity_timelines WHERE user_id = :uid"), uid_param)
        
        await db.execute(text("UPDATE tasks SET batch_id = NULL WHERE batch_id IN (SELECT id FROM submission_batches WHERE submitter_id = :uid)"), uid_param)
        await db.execute(text("DELETE FROM task_histories WHERE changed_by = :uid OR task_id IN (SELECT id FROM tasks WHERE owner_id = :uid)"), uid_param)
        await db.execute(text("DELETE FROM task_evidence WHERE uploaded_by = :uid OR task_id IN (SELECT id FROM tasks WHERE owner_id = :uid)"), uid_param)
        await db.execute(text("DELETE FROM task_reviews WHERE reviewer_id = :uid OR task_id IN (SELECT id FROM tasks WHERE owner_id = :uid)"), uid_param)
        
        await db.execute(text("DELETE FROM approval_queues WHERE reviewer_id = :uid OR batch_id IN (SELECT id FROM submission_batches WHERE submitter_id = :uid)"), uid_param)
        await db.execute(text("DELETE FROM submission_batches WHERE submitter_id = :uid"), uid_param)
        await db.execute(text("DELETE FROM tasks WHERE owner_id = :uid"), uid_param)
        
        await db.execute(text("DELETE FROM kpi_history WHERE changed_by_id = :uid OR kpi_record_id IN (SELECT id FROM kpi_records WHERE user_id = :uid)"), uid_param)
        await db.execute(text("DELETE FROM kpi_action_plans WHERE user_id = :uid"), uid_param)
        await db.execute(text("DELETE FROM kpi_records WHERE user_id = :uid"), uid_param)
        await db.execute(text("DELETE FROM kpi_assignments WHERE user_id = :uid OR assigned_by_id = :uid"), uid_param)
        await db.execute(text("DELETE FROM formula_audit_logs WHERE action_by_id = :uid"), uid_param)
        
        await db.execute(text("DELETE FROM audit_logs WHERE user_id = :uid"), uid_param)
        await db.execute(text("DELETE FROM user_departments WHERE user_id = :uid"), uid_param)
        await db.execute(text("DELETE FROM password_history WHERE user_id = :uid"), uid_param)
        await db.execute(text("DELETE FROM login_history WHERE user_id = :uid"), uid_param)
        await db.execute(text("DELETE FROM notifications WHERE user_id = :uid"), uid_param)
        
        await db.delete(user)
        await db.commit()
        
        return {'message': 'User permanently deleted successfully (Hard Delete).'}
    except Exception as e:
        await db.rollback()
        print("====== HARD DELETE ERROR ======")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Database Error: {str(e)}")

@router.put('/{user_id}/reset-password')
async def reset_user_password(user_id: UUID, payload: PasswordResetAdmin, db: AsyncSession = Depends(get_db), current_user: User = Depends(admin_checker)):
    enforce_permission(current_user, "users.password_reset")
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(status_code=404, detail='User not found.')
        
    if getattr(user, 'is_deleted', False):
        raise HTTPException(status_code=400, detail='Cannot reset password for a deleted account. Please restore the account first.')
        
    if current_user.role.name != 'Super Admin' and getattr(user.role, 'name', '') == 'Super Admin':
        raise HTTPException(status_code=403, detail='You do not have permission to reset a Super Admin account password.')
        
    try:
        validate_password_strength(payload.new_password, **(await get_password_policy(db)))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    is_valid_history = await password_repo.check_password_history(db, user_id, payload.new_password)
    if not is_valid_history:
        raise HTTPException(status_code=400, detail='Cannot reuse any of the last 5 passwords.')
        
    hashed_pass = get_password_hash(payload.new_password)
    await user_repo.update_user(db, user, {'hashed_password': hashed_pass, 'must_change_password': True})
    await password_repo.add_password_history(db, user.id, hashed_pass)
    
    return {'message': 'Password has been reset successfully.'}

@router.post('/{user_id}/restore', response_model=UserResponse)
async def restore_user(user_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, "users.edit")
    if current_user.role.name not in ['Super Admin']:
        raise HTTPException(status_code=403, detail='Only Super Admins can restore accounts.')
        
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if not user: raise HTTPException(status_code=404, detail='User not found.')
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

    user.is_deleted = False
    user.is_active = True
    db.add(user)
    await db.commit()
    
    restored_user = await user_repo.get_user_by_id(db, user_id)
    return serialize_user_obj(restored_user)

@router.post('/{user_id}/set-leave')
async def set_user_leave(user_id: UUID, payload: UserLeaveRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.id != user_id and current_user.role.name not in ['Super Admin', 'Admin']:
        raise HTTPException(status_code=403, detail='You do not have permission to set leave for this user.')
        
    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalars().first()
    if not user: raise HTTPException(status_code=404, detail='User not found.')
    
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
@router.get("/{user_id}/process-tracking", tags=["Super Admin Tracking"])
async def get_user_process_tracking(
    user_id: UUID, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(admin_checker)
):
    user_check = await db.get(User, user_id)
    if not user_check:
        raise HTTPException(status_code=404, detail="User not found.")

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
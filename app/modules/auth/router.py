from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from fastapi.concurrency import run_in_threadpool # ⚡ PERFORMANCE FIX
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from fastapi.security import OAuth2PasswordRequestForm # 🌟 ADDED: For Swagger UI

from app.database.connection import get_db
from app.modules.auth.schemas import LoginRequest, TokenResponse, RefreshTokenRequest, ChangePasswordRequest
from app.modules.users import repo as user_repo
from app.modules.users import password_repo
from app.core.dependencies import get_current_user
from app.modules.users.models import User
from app.modules.departments.models import UserDepartment
from app.core.password_policy import validate_password_strength
from app.core import security
from app.core.config import settings

# 🌟 MODIFIED: Imported TokenBlacklist from unified models
from app.modules.auth.models import LoginHistory, TokenBlacklist
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

router = APIRouter()

# 🌟 SECURITY: Dummy bcrypt hash to prevent timing attacks
DUMMY_PASSWORD_HASH = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjIQ6VSAca"

# 🌟 SECURITY: Ensure cookies are sent securely in production
IS_PRODUCTION = getattr(settings, "ENV", "development").lower() == "production"

@router.get('/me')
async def get_current_user_context(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    stmt = select(User).options(
        selectinload(User.role),
        selectinload(User.position),
        selectinload(User.user_departments).selectinload(UserDepartment.department)
    ).where(User.id == current_user.id)
    
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    role_value = user.role.name if user.role else 'User'
    user_level = 'L2'
    dept_id = None
    dept_name = 'No Department'
    
    if user.user_departments and len(user.user_departments) > 0:
        ud = user.user_departments[0]
        user_level = ud.user_level
        dept_id = str(ud.department_id)
        if ud.department:
            dept_name = ud.department.name

    pos_id = str(user.position_id) if user.position_id else None
    pos_name = user.position.name if user.position else 'No Position'

    return {
        "user_id": str(user.id),
        "full_name": f"{user.first_name} {user.last_name}".strip(),
        "role": role_value,
        "department_id": dept_id,
        "department_name": dept_name,
        "position_id": pos_id,
        "position_name": pos_name,
        "kpi_level": user_level
    }

# ==============================================================================
# 🌟 ORIGINAL JSON LOGIN API (Frontend အတွက်)
# ==============================================================================
@router.post('/login', response_model=TokenResponse)
@limiter.limit("5/minute") 
async def login(request: Request, response: Response, payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    client_ip = request.client.host if request.client else 'Unknown'
    user_agent = request.headers.get('user-agent', 'Unknown')

    stmt = select(User).options(
        selectinload(User.role),
        selectinload(User.position),
        selectinload(User.user_departments).selectinload(UserDepartment.department)
    ).where(User.email == payload.email)
    
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user:
        # ⚡ PERFORMANCE FIX: Offload CPU-bound dummy hash check to threadpool
        await run_in_threadpool(security.verify_password, payload.password, DUMMY_PASSWORD_HASH)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid email or password.')

    if not user.is_active or user.is_deleted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Account disabled. Please contact the administrator.')

    if user.is_locked:
        if user.lockout_until and user.lockout_until > now:
            remaining = int((user.lockout_until - now).total_seconds() / 60)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f'Account locked. Try again in {remaining} minutes.')
        else:
            user.is_locked = False
            user.failed_login_attempts = 0
            user.lockout_until = None
            await db.commit()

    # ⚡ PERFORMANCE FIX: Offload actual password verification to threadpool
    is_valid_password = await run_in_threadpool(security.verify_password, payload.password, user.hashed_password)

    if not is_valid_password:
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= 5:
            user.is_locked = True
            user.lockout_until = now + timedelta(minutes=30)
            
        failed_history = LoginHistory(user_id=user.id, ip_address=client_ip, user_agent=user_agent, is_success=False, attempted_at=now)
        db.add(failed_history)
        await db.commit()
        
        if user.is_locked:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Account locked due to multiple failed login attempts.')
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid email or password.')

    user.failed_login_attempts = 0
    user.is_locked = False
    user.lockout_until = None
    
    success_history = LoginHistory(user_id=user.id, ip_address=client_ip, user_agent=user_agent, is_success=True, attempted_at=now)
    db.add(success_history)
    await db.commit()

    role_value = user.role.name if user.role else str(user.role_id)
    
    user_level = 'L2'
    dept_name = 'No Department'
    dept_id = None
    if user.user_departments and len(user.user_departments) > 0:
        ud = user.user_departments[0]
        user_level = ud.user_level
        dept_id = str(ud.department_id)
        if ud.department:
            dept_name = ud.department.name

    pos_name = user.position.name if user.position else 'No Position'

    token_data = {'sub': user.email, 'user_id': str(user.id), 'role': role_value, 'level': user_level}
    access_token = security.create_access_token(data=token_data)
    refresh_token = security.create_refresh_token(data=token_data)

    # 🛡️ SECURITY FIX: Upgraded to Strict CSRF Protection via samesite="strict"
    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="strict", 
        secure=IS_PRODUCTION 
    )
    
    # 🛡️ SECURITY FIX: Upgraded to Strict CSRF Protection via samesite="strict"
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        samesite="strict",
        secure=IS_PRODUCTION
    )

    user_dict = {
        "id": str(user.id),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": f"{user.first_name} {user.last_name}".strip(),
        "email": user.email,
        "role": {"name": role_value},
        "level": user_level,
        "department_id": dept_id,
        "department_name": dept_name,
        "position_name": pos_name
    }

    return {
        'access_token': access_token, 
        'refresh_token': refresh_token, 
        'token_type': 'bearer',
        'must_change_password': bool(user.must_change_password),
        'user': user_dict 
    }

# ==============================================================================
# 🌟 NEW: SWAGGER UI LOGIN API (Swagger Button မှ ဝင်ရောက်ရန်)
# ==============================================================================
@router.post('/swagger-login', include_in_schema=False)
@limiter.limit("5/minute")
async def login_for_swagger(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(), 
    db: AsyncSession = Depends(get_db)
):
    """
    Swagger UI က 'username' ဆိုတဲ့ field နဲ့ ပို့လိုက်တဲ့ Data ကို 
    ဒီ Route က 'email' အဖြစ်ပြောင်းပြီး Database မှာ တိုက်စစ်ပေးပါမယ်။
    """
    stmt = select(User).where(User.email == form_data.username) 
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user:
        await run_in_threadpool(security.verify_password, form_data.password, DUMMY_PASSWORD_HASH)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Incorrect email or password.')

    if not user.is_active or user.is_deleted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Account disabled or deleted.')

    # Verify Password
    is_valid_password = await run_in_threadpool(security.verify_password, form_data.password, user.hashed_password)

    if not is_valid_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Incorrect email or password.')

    # Generate Access Token
    role_value = str(user.role_id)
    user_level = 'L2'
    
    token_data = {'sub': user.email, 'user_id': str(user.id), 'role': role_value, 'level': user_level}
    access_token = security.create_access_token(data=token_data)

    # Swagger က အတိအကျတောင်းဆိုတဲ့ format
    return {
        "access_token": access_token,
        "token_type": "bearer"
    }

@router.post('/change-password')
async def self_change_password(payload: ChangePasswordRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # ⚡ PERFORMANCE FIX: Offload password verification
    is_valid_old_password = await run_in_threadpool(security.verify_password, payload.old_password, user.hashed_password)
    if not is_valid_old_password:
        raise HTTPException(status_code=400, detail='The current password provided is incorrect.')

    validate_password_strength(payload.new_password)
    
    is_valid_history = await password_repo.check_password_history(db, user.id, payload.new_password)
    if not is_valid_history:
        raise HTTPException(status_code=400, detail='You cannot reuse any of your last 5 passwords. Please choose a new password.')

    # ⚡ PERFORMANCE FIX: Offload password hashing
    hashed_pass = await run_in_threadpool(security.get_password_hash, payload.new_password)
    
    user.hashed_password = hashed_pass
    user.must_change_password = False 

    await db.commit()
    await password_repo.add_password_history(db, user.id, hashed_pass)

    return {'message': 'Password changed successfully.'}

@router.post('/refresh', response_model=TokenResponse)
async def refresh_token(request: Request, response: Response, payload: RefreshTokenRequest = None, db: AsyncSession = Depends(get_db)):
    token_to_verify = None
    if payload and payload.refresh_token:
        token_to_verify = payload.refresh_token
    else:
        token_to_verify = request.cookies.get("refresh_token")

    if not token_to_verify:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Refresh token is missing.')

    # 🛡️ SECURITY FIX: Check if refresh token is blacklisted (Logged out)
    is_blacklisted = await db.execute(select(TokenBlacklist).where(TokenBlacklist.token == token_to_verify))
    if is_blacklisted.scalars().first():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Refresh token has been revoked. Please log in again.')

    try:
        decoded = jwt.decode(token_to_verify, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if decoded.get('type') != 'refresh':
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid token type provided.')
        email = decoded.get('sub')
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid or expired refresh token.')

    stmt = select(User).options(
        selectinload(User.role),
        selectinload(User.user_departments)
    ).where(User.email == email)
    
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user or user.is_deleted or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User not found or account is inactive.')

    role_value = user.role.name if user.role else str(user.role_id)
    user_level = 'L2'
    if user.user_departments and len(user.user_departments) > 0:
        user_level = user.user_departments[0].user_level

    token_data = {'sub': user.email, 'user_id': str(user.id), 'role': role_value, 'level': user_level}
    new_access_token = security.create_access_token(data=token_data)
    new_refresh_token = security.create_refresh_token(data=token_data)

    # 🛡️ SECURITY FIX: Upgraded to Strict CSRF Protection via samesite="strict"
    response.set_cookie(key="access_token", value=f"Bearer {new_access_token}", httponly=True, max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, samesite="strict", secure=IS_PRODUCTION)
    response.set_cookie(key="refresh_token", value=new_refresh_token, httponly=True, max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60, samesite="strict", secure=IS_PRODUCTION)

    return {'access_token': new_access_token, 'refresh_token': new_refresh_token, 'token_type': 'bearer'}

@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    # 1. Extract Access Token
    access_token = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        access_token = auth_header.split(" ")[1]
    else:
        cookie_token = request.cookies.get("access_token")
        if cookie_token and cookie_token.startswith("Bearer "):
            access_token = cookie_token.split(" ")[1]

    # 2. Extract Refresh Token
    refresh_token = request.cookies.get("refresh_token")

    # 🛡️ SECURITY FIX: Invalidate both Access & Refresh Tokens in the Database
    tokens_to_revoke = [t for t in [access_token, refresh_token] if t]
    
    if tokens_to_revoke:
        try:
            # ⚡ PERFORMANCE FIX: Database ကို Loop ပတ်ပြီး အကြိမ်ကြိမ် မခေါ်တော့ဘဲ တစ်ခါတည်း (IN clause ဖြင့်) စစ်ဆေးမည်
            stmt = select(TokenBlacklist.token).where(TokenBlacklist.token.in_(tokens_to_revoke))
            existing_tokens = (await db.execute(stmt)).scalars().all()
            
            # မရှိသေးသော (အသစ်) Token များကိုသာ Blacklist သို့ ထည့်မည်
            new_tokens_to_add = set(tokens_to_revoke) - set(existing_tokens)
            
            for t in new_tokens_to_add:
                db.add(TokenBlacklist(token=t))
                
            if new_tokens_to_add:
                await db.commit()
        except Exception:
            await db.rollback()

    # 🛡️ SECURITY FIX: Explicitly clear the strict cookies on logout
    response.delete_cookie(key="access_token", httponly=True, samesite="strict", secure=IS_PRODUCTION)
    response.delete_cookie(key="refresh_token", httponly=True, samesite="strict", secure=IS_PRODUCTION)
    
    return {"message": "Logged out and tokens revoked successfully."}
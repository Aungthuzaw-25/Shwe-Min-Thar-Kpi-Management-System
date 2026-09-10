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
from app.core.dependencies import get_current_user, get_permission_map
from app.modules.users.models import User
from app.modules.departments.models import UserDepartment
from app.core.password_policy import validate_password_strength
from app.core import security
from app.core.config import settings

# 🌟 MODIFIED: Imported TokenBlacklist from unified models
from app.modules.auth.models import LoginHistory, TokenBlacklist, AuthSession
from app.modules.auth.service import AuthService
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

router = APIRouter()

# 🌟 SECURITY: Dummy bcrypt hash to prevent timing attacks
DUMMY_PASSWORD_HASH = "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjIQ6VSAca"

# 🌟 SECURITY: Ensure cookies are sent securely only on HTTPS, including local HTTP testing
IS_PRODUCTION = getattr(settings, "ENV", "development").lower() == "production"


def cookie_secure_for_request(request: Request) -> bool:
    if IS_PRODUCTION:
        return True

    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto:
        proto = forwarded_proto.split(",")[0].strip().lower()
        if proto == "https":
            host = (request.url.hostname or "").lower()
            return host not in {"localhost", "127.0.0.1", "::1"}
        return False

    host = (request.url.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return False

    return request.url.scheme == "https"


@router.get('/me')
async def get_current_user_context(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    stmt = select(User).options(
        selectinload(User.role),
        selectinload(User.position),
        selectinload(User.user_departments).selectinload(UserDepartment.department)
    ).where(User.id == current_user.id, User.is_deleted.is_(False))
    
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    role_value = user.role.name if user.role else 'User'
    user_level = None
    dept_id = None
    dept_name = 'No Department'
    
    ud = next(
        (
            link for link in (user.user_departments or [])
            if not getattr(link, "is_deleted", False)
        ),
        None,
    )
    if ud:
        raw_level = (ud.user_level or "").strip().upper().replace("LEVEL ", "L")
        user_level = raw_level if raw_level in {"L0", "L1", "L2"} else None
        dept_id = str(ud.department_id)
        if ud.department:
            dept_name = ud.department.name

    pos_id = str(user.position_id) if user.position_id else None
    pos_name = user.position.name if user.position else 'No Position'

    return {
        "user_id": str(user.id),
        "full_name": f"{user.first_name} {user.last_name}".strip(),
        "role": role_value,
        "permissions": sorted(
            code for code, allowed in get_permission_map(user).items() if allowed
        ),
        "department_id": dept_id,
        "department_name": dept_name,
        "position_id": pos_id,
        "position_name": pos_name,
        "kpi_level": user_level,
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

    # Keep JSON login and Swagger login on the same authentication policy.
    auth_result = await AuthService.authenticate_user(
        db, payload.email, payload.password, client_ip, user_agent,
        remember_me=payload.remember_me,
    )
    secure_cookie = cookie_secure_for_request(request)
    response.set_cookie(
        key="access_token", value=auth_result["access_token"], httponly=True,
        max_age=(settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60 if payload.remember_me else None),
        samesite="strict", secure=secure_cookie,
    )
    response.set_cookie(
        key="refresh_token", value=auth_result["refresh_token"], httponly=True,
        max_age=(settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60 if payload.remember_me else None),
        samesite="strict", secure=secure_cookie,
    )
    return auth_result

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
    result = await AuthService.authenticate_user(
        db,
        form_data.username,
        form_data.password,
        ip_address=request.client.host if request.client else "Unknown",
        user_agent=request.headers.get("user-agent", "Unknown"),
    )

    # Swagger က အတိအကျတောင်းဆိုတဲ့ format
    return {
        "access_token": result["access_token"],
        "token_type": "bearer"
    }

@router.post('/change-password')
@limiter.limit("5/minute")
async def self_change_password(
    request: Request,
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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
@limiter.limit("10/minute")
async def refresh_token(request: Request, response: Response, payload: RefreshTokenRequest = None, db: AsyncSession = Depends(get_db)):
    token_to_verify = None
    if payload and payload.refresh_token:
        token_to_verify = payload.refresh_token
    else:
        token_to_verify = request.cookies.get("refresh_token")

    if not token_to_verify:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Refresh token is missing.')

    # 🛡️ SECURITY FIX: Check if refresh token is blacklisted (Logged out)
    token_hash = security.token_digest(token_to_verify)
    is_blacklisted = await db.execute(select(TokenBlacklist).where(TokenBlacklist.token_hash == token_hash))
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

    session_id = decoded.get("sid")
    auth_session = None
    if session_id:
        auth_session = (await db.execute(select(AuthSession).where(
            AuthSession.id == session_id,
            AuthSession.user_id == user.id,
            AuthSession.refresh_token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(timezone.utc).replace(tzinfo=None),
        ).with_for_update())).scalars().first()
        if not auth_session:
            raise HTTPException(status_code=401, detail="Refresh session has been revoked.")

    # Rotation makes a refresh token single-use. A second presentation is
    # rejected by the blacklist check above.
    db.add(TokenBlacklist(token_hash=token_hash))
    await db.commit()

    role_value = user.role.name if user.role else str(user.role_id)
    user_level = 'L2'
    if user.user_departments and len(user.user_departments) > 0:
        user_level = user.user_departments[0].user_level

    token_data = {'sub': user.email, 'user_id': str(user.id), 'role': role_value, 'level': user_level}
    new_access_token = security.create_access_token(data=token_data)
    new_refresh_token = security.create_refresh_token(
        data=token_data, remember_me=bool(auth_session.remember_me) if auth_session else True
    )
    if auth_session:
        auth_session.refresh_token_hash = security.token_digest(new_refresh_token)
        auth_session.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()

    # 🛡️ SECURITY FIX: Upgraded to Strict CSRF Protection via samesite="strict"
    secure_cookie = cookie_secure_for_request(request)
    persistent = bool(auth_session.remember_me) if auth_session else True
    response.set_cookie(key="access_token", value=new_access_token, httponly=True, max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60 if persistent else None, samesite="strict", secure=secure_cookie)
    response.set_cookie(key="refresh_token", value=new_refresh_token, httponly=True, max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60 if persistent else None, samesite="strict", secure=secure_cookie)

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
        if cookie_token:
            access_token = cookie_token.removeprefix("Bearer ").strip()

    # 2. Extract Refresh Token
    refresh_token = request.cookies.get("refresh_token")

    # 🛡️ SECURITY FIX: Invalidate both Access & Refresh Tokens in the Database
    tokens_to_revoke = [t for t in [access_token, refresh_token] if t]
    
    if tokens_to_revoke:
        # ⚡ PERFORMANCE FIX: Check all presented credentials in one query.
        token_hashes = [security.token_digest(token) for token in tokens_to_revoke]
        stmt = select(TokenBlacklist.token_hash).where(TokenBlacklist.token_hash.in_(token_hashes))
        existing_tokens = (await db.execute(stmt)).scalars().all()
        for token_hash in set(token_hashes) - set(existing_tokens):
            db.add(TokenBlacklist(token_hash=token_hash))

        # Revoke the server-side session as well as its credentials.
        try:
            session_payload = jwt.decode(
                refresh_token or access_token, settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM],
                options={"verify_exp": False},
            )
        except JWTError:
            session_payload = {}
        sid = session_payload.get("sid")
        if sid:
            session = await db.get(AuthSession, sid)
            if session:
                session.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()

    # 🛡️ SECURITY FIX: Explicitly clear the strict cookies on logout
    secure_cookie = cookie_secure_for_request(request)
    response.delete_cookie(key="access_token", httponly=True, samesite="strict", secure=secure_cookie)
    response.delete_cookie(key="refresh_token", httponly=True, samesite="strict", secure=secure_cookie)
    
    return {"message": "Logged out and tokens revoked successfully."}

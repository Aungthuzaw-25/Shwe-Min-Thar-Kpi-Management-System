from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool  # ⚡ PERFORMANCE FIX
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm  # 🌟 ADDED: For Swagger UI
from jwt import PyJWTError as JWTError
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core import security
from app.core.config import settings
from app.core.dependencies import get_current_user, get_permission_map
from app.core.password_policy import get_password_policy, validate_password_strength
from app.core.timezone import utc_now_naive
from app.database.connection import get_db
from app.models.audit_log import AuditLog

# 🌟 MODIFIED: Imported TokenBlacklist from unified models
from app.modules.auth.models import AuthSession, TokenBlacklist
from app.modules.auth.oauth_state import (
    INVALID_OAUTH_STATE_MESSAGE,
    consume_oauth_state,
    create_oauth_state,
    get_oauth_redis,
)
from app.modules.auth.oidc import (
    OIDCProvider,
    OIDCValidationError,
    create_nonce,
    create_pkce_verifier,
)
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshTokenRequest,
    TokenResponse,
)
from app.modules.auth.service import AuthService
from app.modules.departments.models import UserDepartment
from app.modules.users import password_repo
from app.modules.users.models import OAuthAccount, User
from app.modules.users.router import revoke_user_authentication

limiter = Limiter(key_func=get_remote_address)

router = APIRouter()
PROFILE_RETURN_PATH = "/profile"

def _provider(name: str) -> OIDCProvider:
    if name == "google":
        if not (settings.OAUTH_ENABLED and settings.GOOGLE_OIDC_ENABLED and settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and settings.GOOGLE_REDIRECT_URI):
            raise HTTPException(status_code=404, detail="OAuth provider is not enabled.")
        return OIDCProvider(
            name="google", client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET, redirect_uri=settings.GOOGLE_REDIRECT_URI,
            discovery_url="https://accounts.google.com/.well-known/openid-configuration",
            scopes=("openid", "email", "profile"),
        )
    if not (settings.OAUTH_ENABLED and settings.MICROSOFT_OIDC_ENABLED and settings.MICROSOFT_CLIENT_ID and settings.MICROSOFT_CLIENT_SECRET and settings.MICROSOFT_REDIRECT_URI):
        raise HTTPException(status_code=404, detail="OAuth provider is not enabled.")
    tenant = settings.MICROSOFT_TENANT_ID or "organizations"
    return OIDCProvider(
        name="microsoft", client_id=settings.MICROSOFT_CLIENT_ID,
        client_secret=settings.MICROSOFT_CLIENT_SECRET, redirect_uri=settings.MICROSOFT_REDIRECT_URI,
        discovery_url=f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration",
        scopes=("openid", "email", "profile"),
        microsoft=True,
    )


def _oauth_error_redirect(message: str) -> RedirectResponse:
    from urllib.parse import quote
    return RedirectResponse(url=f"/auth/login?oauth_error={quote(message)}", status_code=303)


def _dashboard_path(user: User) -> str:
    role_name = user.role.name if user.role else ""
    if role_name == "Super Admin":
        return "/superadmin/dashboard"
    if role_name in {"Admin", "HR"}:
        return "/admin/dashboard"
    return "/dashboard"


def _set_auth_cookies(response: Response, request: Request, auth_result: dict[str, object], remember_me: bool) -> None:
    secure_cookie = cookie_secure_for_request(request)
    response.set_cookie(key="access_token", value=str(auth_result["access_token"]), httponly=True,
                        max_age=(settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60 if remember_me else None),
                        samesite=AUTH_COOKIE_SAMESITE, secure=secure_cookie)
    response.set_cookie(key="refresh_token", value=str(auth_result["refresh_token"]), httponly=True,
                        max_age=(settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60 if remember_me else None),
                        samesite=AUTH_COOKIE_SAMESITE, secure=secure_cookie)

# 🌟 SECURITY: Ensure cookies are sent securely only on HTTPS, including local HTTP testing
IS_PRODUCTION = getattr(settings, "ENV", "development").lower() == "production"
AUTH_COOKIE_SAMESITE = "lax"


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


@router.get("/me/accounts")
async def get_current_user_accounts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        await db.execute(
            select(OAuthAccount)
            .where(
                OAuthAccount.user_id == current_user.id,
                OAuthAccount.revoked_at.is_(None),
            )
            .order_by(OAuthAccount.provider)
        )
    ).scalars().all()
    return {
        "user_email": current_user.email,
        "local_password_enabled": bool(current_user.local_password_enabled),
        "created_at": current_user.created_at,
        "connected_accounts": {
            row.provider: {
                "email": row.provider_email,
                "connected_at": row.linked_at or row.created_at,
            }
            for row in rows
        },
    }


# ==============================================================================
# 🌟 ORIGINAL JSON LOGIN API (Frontend အတွက်)
# ==============================================================================
@router.post('/login', response_model=TokenResponse)
@limiter.limit("5/minute") 
async def login(request: Request, response: Response, payload: LoginRequest, db: AsyncSession = Depends(get_db)):
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
        samesite=AUTH_COOKIE_SAMESITE, secure=secure_cookie,
    )
    response.set_cookie(
        key="refresh_token", value=auth_result["refresh_token"], httponly=True,
        max_age=(settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60 if payload.remember_me else None),
        samesite=AUTH_COOKIE_SAMESITE, secure=secure_cookie,
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


async def _resolve_oauth_user(db: AsyncSession, identity) -> tuple[User | None, OAuthAccount | None]:
    tenant_key = identity.tenant_id or ""
    identity_row = (await db.execute(
        select(OAuthAccount).where(
            OAuthAccount.provider == identity.provider,
            OAuthAccount.issuer == identity.issuer,
            OAuthAccount.tenant_id == tenant_key,
            OAuthAccount.provider_subject == identity.subject,
            OAuthAccount.revoked_at.is_(None),
        )
    )).scalars().first()
    if identity_row:
        user = (await db.execute(select(User).options(
            selectinload(User.role),
            selectinload(User.position),
            selectinload(User.user_departments).selectinload(UserDepartment.department),
        ).where(User.id == identity_row.user_id))).scalars().first()
        return user, identity_row
    # Email is only a provisioning hint. It must never authenticate or link an account.
    return None, None


async def _complete_oauth_login(
    request: Request,
    db: AsyncSession,
    provider_name: str,
    code: str | None,
    state: str | None,
    error: str | None,
    callback_issuer: str | None = None,
):
    if error:
        return _oauth_error_redirect("Provider sign-in was cancelled.")
    try:
        redis = get_oauth_redis(request)
        state_data = await consume_oauth_state(redis, state or "")
        if state_data.get("provider") != provider_name:
            raise HTTPException(status_code=400, detail=INVALID_OAUTH_STATE_MESSAGE)
        provider = _provider(provider_name)
        await provider.validate_callback_issuer(callback_issuer)
        if state_data.get("flow_type") == "LINK":
            return await _complete_link(
                request,
                db,
                provider_name,
                code,
                state,
                error,
                None,
                state_data=state_data,
                provider=provider,
            )
        if state_data.get("flow_type") != "LOGIN":
            raise HTTPException(status_code=400, detail=INVALID_OAUTH_STATE_MESSAGE)
        token_response = await provider.exchange_code(code or "", state_data["pkce_verifier"])
        identity = await provider.validate_identity(token_response, state_data["nonce"])
        user, identity_row = await _resolve_oauth_user(db, identity)
        if not user:
            db.add(AuditLog(action="OAUTH_LOGIN_REJECTED", table_name="oauth_accounts",
                            new_value={"provider": provider_name, "reason": "unlinked_identity"}))
            await db.commit()
            return _oauth_error_redirect("Your account has not been provisioned for this application. Please contact an administrator.")
        if not user.is_active or user.is_deleted:
            return _oauth_error_redirect("Your account is disabled. Please contact an administrator.")
        identity_row.last_login_at = utc_now_naive()
        auth_result = await AuthService.issue_authenticated_session(
            db, user, request.client.host if request.client else "Unknown",
            request.headers.get("user-agent", "Unknown"), bool(state_data.get("remember_me")), audit_action="OAUTH_LOGIN",
        )
        response = RedirectResponse(url=_dashboard_path(user), status_code=303)
        _set_auth_cookies(response, request, auth_result, bool(state_data.get("remember_me")))
        return response
    except (OIDCValidationError, HTTPException) as exc:
        if isinstance(exc, HTTPException) and exc.status_code == 503:
            return _oauth_error_redirect("OAuth sign-in is temporarily unavailable.")
        return _oauth_error_redirect("Unable to validate provider sign-in.")
    except Exception:
        return _oauth_error_redirect("Unable to complete provider sign-in.")


@router.get("/google/start")
async def google_start(request: Request, remember_me: bool = False, redis=Depends(get_oauth_redis)):
    provider = _provider("google")
    nonce, verifier = create_nonce(), create_pkce_verifier()
    state = await create_oauth_state(redis, provider="google", nonce=nonce, pkce_verifier=verifier, remember_me=remember_me)
    return RedirectResponse(await provider.authorization_url(state, nonce, verifier), status_code=303)


@router.get("/google/callback")
async def google_callback(request: Request, code: str | None = None, state: str | None = None,
                          error: str | None = None, iss: str | None = None,
                          db: AsyncSession = Depends(get_db)):
    return await _complete_oauth_login(request, db, "google", code, state, error, iss)


@router.get("/microsoft/start")
async def microsoft_start(request: Request, remember_me: bool = False, redis=Depends(get_oauth_redis)):
    provider = _provider("microsoft")
    nonce, verifier = create_nonce(), create_pkce_verifier()
    state = await create_oauth_state(redis, provider="microsoft", nonce=nonce, pkce_verifier=verifier, remember_me=remember_me)
    return RedirectResponse(await provider.authorization_url(state, nonce, verifier), status_code=303)


@router.get("/microsoft/callback")
async def microsoft_callback(request: Request, code: str | None = None, state: str | None = None,
                             error: str | None = None, iss: str | None = None,
                             db: AsyncSession = Depends(get_db)):
    return await _complete_oauth_login(request, db, "microsoft", code, state, error, iss)


async def _recent_authentication(db: AsyncSession, user_id) -> bool:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
    return (await db.execute(select(AuthSession).where(
        AuthSession.user_id == user_id,
        AuthSession.revoked_at.is_(None),
        AuthSession.last_used_at >= cutoff,
    ))).scalars().first() is not None


async def _link_start(provider_name: str, user: User, db: AsyncSession, redis):
    if not await _recent_authentication(db, user.id):
        raise HTTPException(status_code=403, detail="Recent authentication is required before linking an account.")
    provider = _provider(provider_name)
    nonce, verifier = create_nonce(), create_pkce_verifier()
    state = await create_oauth_state(redis, provider=provider_name, nonce=nonce,
                                     pkce_verifier=verifier, mode="link", user_id=str(user.id),
                                     return_path=PROFILE_RETURN_PATH)
    return RedirectResponse(await provider.authorization_url(state, nonce, verifier), status_code=303)


@router.get("/google/link/start")
async def google_link_start(user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db), redis=Depends(get_oauth_redis)):
    return await _link_start("google", user, db, redis)


@router.get("/microsoft/link/start")
async def microsoft_link_start(user: User = Depends(get_current_user),
                               db: AsyncSession = Depends(get_db), redis=Depends(get_oauth_redis)):
    return await _link_start("microsoft", user, db, redis)


async def _complete_link(request: Request, db: AsyncSession, provider_name: str,
                         code: str | None, state: str | None, error: str | None,
                         current_user: User | None, state_data: dict | None = None,
                         provider: OIDCProvider | None = None):
    if error:
        return _oauth_error_redirect("Provider sign-in was cancelled.")
    try:
        if state_data is None:
            state_data = await consume_oauth_state(get_oauth_redis(request), state or "")
        if state_data.get("provider") != provider_name or state_data.get("flow_type") != "LINK":
            raise HTTPException(status_code=400, detail=INVALID_OAUTH_STATE_MESSAGE)
        provider = provider or _provider(provider_name)
        await provider.validate_callback_issuer(request.query_params.get("iss"))
        identity = await provider.validate_identity(
            await provider.exchange_code(code or "", state_data["pkce_verifier"]), state_data["nonce"]
        )
        user_id = state_data.get("user_id")
        if not user_id or (current_user is not None and str(current_user.id) != str(user_id)):
            raise HTTPException(status_code=403, detail="Authenticated user does not match OAuth link state.")
        duplicate = (await db.execute(select(OAuthAccount).where(
            OAuthAccount.provider == provider_name,
            OAuthAccount.issuer == identity.issuer,
            OAuthAccount.tenant_id == (identity.tenant_id or ""),
            OAuthAccount.provider_subject == identity.subject,
        ))).scalars().first()
        if duplicate and str(duplicate.user_id) != str(user_id):
            db.add(AuditLog(action="OAUTH_IDENTITY_CONFLICT", table_name="oauth_accounts",
                            new_value={"provider": provider_name, "reason": "already_linked"}))
            await db.commit()
            return _oauth_error_redirect("This provider account is already linked to another user.")
        user = (await db.execute(select(User).where(
            User.id == user_id,
            User.is_deleted.is_(False),
        ))).scalars().first()
        if not user:
            return _oauth_error_redirect("Unable to link this provider account.")
        if not user.is_active or not await _recent_authentication(db, user.id):
            return _oauth_error_redirect("Recent authentication is required before linking an account.")
        if duplicate:
            duplicate.revoked_at = None
            duplicate.provider_email = identity.email
            duplicate.email_verified = identity.email_verified
        else:
            db.add(OAuthAccount(user_id=user.id, provider=provider_name, issuer=identity.issuer,
                                tenant_id=identity.tenant_id or "", provider_subject=identity.subject,
                                provider_email=identity.email, email_verified=identity.email_verified,
                                linked_at=utc_now_naive()))
        db.add(AuditLog(user_id=user.id, action="OAUTH_IDENTITY_LINKED",
                        table_name="oauth_accounts", new_value={"provider": provider_name}))
        await db.commit()
        return_path = state_data.get("return_path") or PROFILE_RETURN_PATH
        if not return_path.startswith("/") or return_path.startswith("//"):
            return_path = PROFILE_RETURN_PATH
        separator = "&" if "?" in return_path else "?"
        return RedirectResponse(
            url=f"{return_path}{separator}oauth_linked=1",
            status_code=303,
        )
    except (OIDCValidationError, HTTPException):
        return _oauth_error_redirect("Unable to link provider account.")
    except Exception:
        return _oauth_error_redirect("Unable to link provider account.")


@router.get("/google/link/callback")
async def google_link_callback(request: Request, code: str | None = None, state: str | None = None,
                               error: str | None = None, db: AsyncSession = Depends(get_db),
                               current_user: User = Depends(get_current_user)):
    return await _complete_link(request, db, "google", code, state, error, current_user)


@router.get("/microsoft/link/callback")
async def microsoft_link_callback(request: Request, code: str | None = None, state: str | None = None,
                                  error: str | None = None, db: AsyncSession = Depends(get_db),
                                  current_user: User = Depends(get_current_user)):
    return await _complete_link(request, db, "microsoft", code, state, error, current_user)


async def _unlink_provider(user: User, provider_name: str, db: AsyncSession):
    if not await _recent_authentication(db, user.id):
        raise HTTPException(status_code=403, detail="Recent authentication is required.")
    identities = (await db.execute(select(OAuthAccount).where(
        OAuthAccount.user_id == user.id, OAuthAccount.revoked_at.is_(None)
    ))).scalars().all()
    if len(identities) <= 1 and not getattr(user, "local_password_enabled", False):
        raise HTTPException(status_code=400, detail="Cannot remove the last usable authentication method.")
    target = next((item for item in identities if item.provider == provider_name), None)
    if not target:
        raise HTTPException(status_code=404, detail="Provider account is not linked.")
    target.revoked_at = utc_now_naive()
    db.add(AuditLog(user_id=user.id, action="OAUTH_IDENTITY_UNLINKED",
                    table_name="oauth_accounts", new_value={"provider": provider_name}))
    await db.commit()
    return {"status": "unlinked"}


@router.delete("/google/link")
async def google_unlink(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _unlink_provider(user, "google", db)


@router.delete("/microsoft/link")
async def microsoft_unlink(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _unlink_provider(user, "microsoft", db)

@router.post('/change-password')
@limiter.limit("5/minute")
async def self_change_password(
    request: Request,
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not getattr(user, "local_password_enabled", True) or not user.hashed_password:
        raise HTTPException(status_code=400, detail="This account does not have a local password.")
    # ⚡ PERFORMANCE FIX: Offload password verification
    is_valid_old_password = await run_in_threadpool(security.verify_password, payload.old_password, user.hashed_password)
    if not is_valid_old_password:
        raise HTTPException(status_code=400, detail='The current password provided is incorrect.')

    if (
        payload.confirm_new_password is not None
        and payload.new_password != payload.confirm_new_password
    ):
        raise HTTPException(status_code=400, detail='New password confirmation does not match.')
    validate_password_strength(payload.new_password, **(await get_password_policy(db)))
    
    is_valid_history = await password_repo.check_password_history(db, user.id, payload.new_password)
    if not is_valid_history:
        raise HTTPException(status_code=400, detail='You cannot reuse any of your last 5 passwords. Please choose a new password.')

    # ⚡ PERFORMANCE FIX: Offload password hashing
    hashed_pass = await run_in_threadpool(security.get_password_hash, payload.new_password)
    
    user.hashed_password = hashed_pass
    user.local_password_enabled = True
    user.must_change_password = False 

    # Persist the password and its history entry atomically.  Committing the
    # user first could leave password-expiry/history checks inconsistent if the
    # history insert failed.
    await password_repo.add_password_history(
        db, user.id, hashed_pass, commit=False
    )
    await revoke_user_authentication(db, user.id)
    await db.commit()

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
        decoded = security.decode_token(token_to_verify, "refresh", require_session=True)
        email = decoded["sub"]
        session_id = UUID(decoded["sid"])
        token_user_id = UUID(decoded["user_id"])
    except (JWTError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        ) from None
    stmt = select(User).options(
        selectinload(User.role),
        selectinload(User.user_departments)
    ).where(User.email == email)
    
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user or user.is_deleted or not user.is_active or user.id != token_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token.")

    auth_session = (await db.execute(select(AuthSession).where(
        AuthSession.id == session_id,
        AuthSession.user_id == user.id,
        AuthSession.refresh_token_hash == token_hash,
        AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > datetime.now(UTC).replace(tzinfo=None),
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

    token_data = {'sub': user.email, 'user_id': str(user.id), 'sid': str(auth_session.id), 'role': role_value, 'level': user_level}
    new_access_token = security.create_access_token(data=token_data)
    new_refresh_token = security.create_refresh_token(
        data=token_data, remember_me=bool(auth_session.remember_me) if auth_session else True
    )
    if auth_session:
        auth_session.refresh_token_hash = security.token_digest(new_refresh_token)
        auth_session.last_used_at = datetime.now(UTC).replace(tzinfo=None)
        await db.commit()

    # Lax permits the top-level GET redirect back from an OAuth provider while
    # keeping the cookies HttpOnly and Secure in production.
    secure_cookie = cookie_secure_for_request(request)
    persistent = bool(auth_session.remember_me) if auth_session else True
    response.set_cookie(key="access_token", value=new_access_token, httponly=True, max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60 if persistent else None, samesite=AUTH_COOKIE_SAMESITE, secure=secure_cookie)
    response.set_cookie(key="refresh_token", value=new_refresh_token, httponly=True, max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60 if persistent else None, samesite=AUTH_COOKIE_SAMESITE, secure=secure_cookie)

    return {'access_token': new_access_token, 'refresh_token': new_refresh_token, 'token_type': 'bearer'}

@router.post("/logout")
async def logout(request: Request, response: Response, payload: RefreshTokenRequest | None = None, db: AsyncSession = Depends(get_db)):
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
    refresh_token = (payload.refresh_token if payload and payload.refresh_token else request.cookies.get("refresh_token"))

    # 🛡️ SECURITY FIX: Invalidate both Access & Refresh Tokens in the Database
    tokens_to_revoke = [t for t in [access_token, refresh_token] if t]
    
    if tokens_to_revoke:
        # ⚡ PERFORMANCE FIX: Check all presented credentials in one query.
        token_hashes = [security.token_digest(token) for token in tokens_to_revoke]
        stmt = select(TokenBlacklist.token_hash).where(TokenBlacklist.token_hash.in_(token_hashes))
        existing_tokens = (await db.execute(stmt)).scalars().all()
        for token_hash in set(token_hashes) - set(existing_tokens):
            db.add(TokenBlacklist(token_hash=token_hash))

        # Signature and the configured algorithm remain verified here. Expiration is
        # intentionally ignored only so an expired presented credential can still revoke its session.
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
                session.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        await db.commit()

    # 🛡️ SECURITY FIX: Explicitly clear the strict cookies on logout
    secure_cookie = cookie_secure_for_request(request)
    response.delete_cookie(key="access_token", httponly=True, samesite=AUTH_COOKIE_SAMESITE, secure=secure_cookie)
    response.delete_cookie(key="refresh_token", httponly=True, samesite=AUTH_COOKIE_SAMESITE, secure=secure_cookie)
    
    return {"message": "Logged out and tokens revoked successfully."}

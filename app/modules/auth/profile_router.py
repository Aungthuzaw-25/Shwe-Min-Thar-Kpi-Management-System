import re
import secrets
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import security
from app.core.dependencies import get_current_user
from app.database.connection import get_db
from app.models.audit_log import AuditLog
from app.modules.auth.models import AuthSession, PasswordHistory
from app.modules.auth.profile_schemas import (
    AccountPasswordChange,
    AccountProfilePatch,
    AccountReauthentication,
    ProfilePatch,
    ProfileResponse,
)
from app.modules.auth.router import limiter
from app.modules.departments.models import UserDepartment
from app.modules.users.avatar import avatar_color_index, avatar_initials, avatar_url
from app.modules.users.models import OAuthAccount, User

router = APIRouter()
account_router = APIRouter()
MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_AVATAR_DIMENSION = 4096
MAX_AVATAR_PIXELS = MAX_AVATAR_DIMENSION * MAX_AVATAR_DIMENSION
AVATAR_SIZE = (256, 256)
AVATAR_FORMATS = {"JPEG", "PNG", "WEBP"}
AVATAR_MEDIA_TYPE = "image/webp"
AVATAR_RESPONSE_HEADERS = {
    "Cache-Control": "private, max-age=86400",
    "X-Content-Type-Options": "nosniff",
}
PROFILE_AVATAR_URL = "/api/v1/auth/me/avatar"
ACCOUNT_AVATAR_URL = "/api/v1/account/avatars"
PASSWORD_CHANGE_FAILED_ACTION = "PASSWORD_CHANGE_FAILED"
PASSWORD_CHANGE_FAILURE_WINDOW = timedelta(minutes=15)
PASSWORD_SESSION_ROTATED_ACTION = "PASSWORD_SESSION_ROTATED"
UNKNOWN_CLIENT = "Unknown"
REAUTHENTICATION_VALID_FOR_SECONDS = 600
AVATAR_NOT_FOUND_DETAIL = "Avatar not found."
INVALID_SESSION_DETAIL = "The current session is invalid."


def _avatar_directory() -> Path:
    return Path("uploads/profile_avatars").resolve()


def normalize_avatar_image(content: bytes) -> bytes:
    if not content or len(content) > MAX_AVATAR_BYTES:
        raise ValueError("Avatar image exceeds the allowed size.")

    try:
        with Image.open(BytesIO(content)) as image:
            if image.format not in AVATAR_FORMATS:
                raise ValueError("Unsupported avatar image format.")
            if (
                image.width > MAX_AVATAR_DIMENSION
                or image.height > MAX_AVATAR_DIMENSION
                or image.width * image.height > MAX_AVATAR_PIXELS
            ):
                raise ValueError("Avatar image dimensions are too large.")
            image.verify()
        with Image.open(BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image)
            image = ImageOps.fit(image, AVATAR_SIZE, method=Image.Resampling.LANCZOS)
            mode = "RGBA" if "transparency" in image.info else "RGB"
            image = image.convert(mode)
            clean = Image.new(mode, AVATAR_SIZE)
            clean.paste(image)
            output = BytesIO()
            clean.save(output, format="WEBP", quality=85, method=6)
            return output.getvalue()
    except (
        OSError,
        Image.DecompressionBombError,
        SyntaxError,
        EOFError,
    ) as exc:
        raise ValueError("Invalid image data.") from exc


def _stored_avatar_path(filename: str | None) -> Path | None:
    if not filename or Path(filename).name != filename or not filename.endswith(".webp"):
        return None
    return _avatar_directory() / filename


def _profile_response(user: User) -> ProfileResponse:
    avatar_version = _avatar_version(getattr(user, "avatar_updated_at", None))
    return ProfileResponse(
        id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        region_town=user.region_town,
        phone_number=user.phone_number,
        bio=user.bio,
        avatar_url=(
            f"{PROFILE_AVATAR_URL}?v={avatar_version}"
            if user.avatar_path
            else None
        ),
    )


def _avatar_version(updated_at: datetime | None) -> int:
    if updated_at is None:
        return 0
    timestamp = updated_at.replace(tzinfo=UTC) if updated_at.tzinfo is None else updated_at
    return int(timestamp.timestamp())


async def apply_profile_patch(
    db: AsyncSession,
    user: User,
    payload: ProfilePatch,
    ip_address: str | None = None,
) -> list[str]:
    if not payload.model_fields_set:
        raise HTTPException(status_code=400, detail="At least one profile field is required.")
    for required_field in ("first_name", "last_name"):
        if required_field in payload.model_fields_set and getattr(payload, required_field) is None:
            raise HTTPException(status_code=422, detail=f"{required_field} cannot be empty.")

    changed_fields = []
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if getattr(user, field) != value:
            setattr(user, field, value)
            changed_fields.append(field)
    if changed_fields:
        db.add(AuditLog(
            user_id=user.id,
            action="PROFILE_UPDATED",
            table_name="users",
            record_id=str(user.id),
            new_value={"fields": sorted(changed_fields)},
            ip_address=ip_address,
        ))
        await db.commit()
        await db.refresh(user)
    return sorted(changed_fields)


@router.get(
    "/me/profile",
    response_model=ProfileResponse,
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
    },
)
async def get_profile(
    user: Annotated[User, Depends(get_current_user)],
):
    return _profile_response(user)


@router.patch(
    "/me/profile",
    response_model=ProfileResponse,
    responses={
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        422: {"description": "Unprocessable Entity"},
        429: {"description": "Too Many Requests"},
    },
)
@limiter.limit("10/minute")
async def patch_profile(
    request: Request,
    payload: ProfilePatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await apply_profile_patch(
        db,
        user,
        payload,
        request.client.host if request.client else None,
    )
    return _profile_response(user)


@router.post(
    "/me/avatar",
    status_code=status.HTTP_200_OK,
    responses={
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        413: {"description": "Payload Too Large"},
        422: {"description": "Unprocessable Entity"},
        429: {"description": "Too Many Requests"},
    },
)
@limiter.limit("5/minute")
async def upload_avatar(
    request: Request,
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if file.size is not None and file.size > MAX_AVATAR_BYTES:
        await file.close()
        raise HTTPException(status_code=413, detail="Avatar image exceeds the allowed size.")
    content = await file.read(MAX_AVATAR_BYTES + 1)
    await file.close()
    try:
        normalized = await run_in_threadpool(normalize_avatar_image, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    directory = _avatar_directory()
    filename = f"{secrets.token_hex(16)}.webp"
    new_path = directory / filename
    old_path = _stored_avatar_path(user.avatar_path)
    try:
        await run_in_threadpool(directory.mkdir, parents=True, exist_ok=True)
        await run_in_threadpool(new_path.write_bytes, normalized)
        user.avatar_path = filename
        user.avatar_updated_at = datetime.now(UTC).replace(tzinfo=None)
        db.add(AuditLog(
            user_id=user.id,
            action="PROFILE_AVATAR_UPDATED",
            table_name="users",
            record_id=str(user.id),
            new_value={"format": "webp"},
            ip_address=request.client.host if request.client else None,
        ))
        await db.commit()
    except Exception:
        await db.rollback()
        await run_in_threadpool(new_path.unlink, missing_ok=True)
        raise
    if old_path and old_path != new_path:
        await run_in_threadpool(old_path.unlink, missing_ok=True)
    return {
        "avatar_url": (
            f"{ACCOUNT_AVATAR_URL}/{filename}"
            f"?v={_avatar_version(getattr(user, 'avatar_updated_at', None))}"
        )
    }


@router.get(
    "/me/avatar",
    response_class=FileResponse,
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        404: {"description": "Not Found"},
    },
)
async def get_avatar(user: Annotated[User, Depends(get_current_user)]):
    image_path = _stored_avatar_path(user.avatar_path)
    if image_path is None or not image_path.is_file():
        raise HTTPException(status_code=404, detail=AVATAR_NOT_FOUND_DETAIL)
    return FileResponse(
        image_path,
        media_type=AVATAR_MEDIA_TYPE,
        headers=AVATAR_RESPONSE_HEADERS,
    )


@router.delete(
    "/me/avatar",
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        404: {"description": "Not Found"},
        429: {"description": "Too Many Requests"},
    },
)
@limiter.limit("5/minute")
async def delete_avatar(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    old_path = _stored_avatar_path(user.avatar_path)
    if old_path is None:
        raise HTTPException(status_code=404, detail=AVATAR_NOT_FOUND_DETAIL)
    user.avatar_path = None
    user.avatar_updated_at = None
    db.add(AuditLog(
        user_id=user.id,
        action="PROFILE_AVATAR_DELETED",
        table_name="users",
        record_id=str(user.id),
        ip_address=request.client.host if request.client else None,
    ))
    await db.commit()
    await run_in_threadpool(old_path.unlink, missing_ok=True)
    return {"status": "deleted"}


@router.get(
    "/avatars/{filename}",
    response_class=FileResponse,
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        404: {"description": "Not Found"},
    },
)
async def get_account_avatar(
    filename: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not re.fullmatch(r"[a-f0-9]{32}\.webp", filename):
        raise HTTPException(status_code=404, detail=AVATAR_NOT_FOUND_DETAIL)
    owner = (await db.execute(
        select(User.id).where(
            User.avatar_path == filename,
            User.is_deleted.is_(False),
        )
    )).scalars().first()
    image_path = _stored_avatar_path(filename) if owner else None
    if (
        image_path is None
        or filename != image_path.name
        or not image_path.is_file()
    ):
        raise HTTPException(status_code=404, detail=AVATAR_NOT_FOUND_DETAIL)
    return FileResponse(
        image_path,
        media_type=AVATAR_MEDIA_TYPE,
        headers=AVATAR_RESPONSE_HEADERS,
    )


def _avatar_initials(display_name: str, email: str = "") -> str:
    return avatar_initials(display_name, email)


def _avatar_color_index(user_id: UUID) -> int:
    return avatar_color_index(user_id)


account_router.include_router(router)


@account_router.get(
    "/me",
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        404: {"description": "Not Found"},
    },
)
async def get_account(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    user = (await db.execute(
        select(User)
        .options(
            selectinload(User.role),
            selectinload(User.position),
            selectinload(User.user_departments).selectinload(UserDepartment.department),
        )
        .where(User.id == current_user.id, User.is_deleted.is_(False))
    )).scalars().first()
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found.")

    membership = next(
        (item for item in (user.user_departments or []) if not item.is_deleted),
        None,
    )
    department = membership.department if membership else None
    role_name = user.role.name if user.role else "User"
    is_super_admin = role_name == "Super Admin"
    display_name = f"{user.first_name} {user.last_name}".strip()

    identity_rows = (await db.execute(
        select(OAuthAccount)
        .where(
            OAuthAccount.user_id == user.id,
            OAuthAccount.revoked_at.is_(None),
        )
        .order_by(OAuthAccount.provider)
    )).scalars().all()
    linked_providers = {
        "google": None,
        "microsoft": None,
    }
    for identity in identity_rows:
        if identity.provider in linked_providers:
            linked_providers[identity.provider] = {
                "email": identity.provider_email,
                "linked_at": identity.linked_at,
            }

    password_changed_at = user.password_changed_at
    if password_changed_at is None and user.local_password_enabled:
        password_changed_at = (await db.execute(
            select(PasswordHistory.created_at)
            .where(PasswordHistory.user_id == user.id)
            .order_by(PasswordHistory.created_at.desc())
            .limit(1)
        )).scalars().first()

    from app.modules.auth.router import oauth_provider_availability

    configured_providers = oauth_provider_availability()
    return {
        "display_name": display_name,
        "email": user.email,
        "phone": user.phone_number,
        "phone_number": user.phone_number,
        "role": role_name,
        "department": department.name if department else None,
        "position": user.position.name if user.position else None,
        "avatar_url": avatar_url(
            user.avatar_path,
            getattr(user, "avatar_updated_at", None),
        ),
        "initials": _avatar_initials(display_name, user.email),
        "avatar_color_index": _avatar_color_index(user.id),
        "password_changed_at": password_changed_at,
        "linked_providers": linked_providers,
        "configured_providers": configured_providers,
        "local_password_enabled": bool(user.local_password_enabled),
        "is_super_admin": is_super_admin,
        "show_connected_accounts": is_super_admin,
        "features_available": {
            "profile_editing": True,
            "avatar_management": True,
            "password_management": True,
            "oauth_login": any(configured_providers.values()),
            "connected_account_management": is_super_admin,
        },
    }


@account_router.patch(
    "/me",
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        422: {"description": "Unprocessable Entity"},
        429: {"description": "Too Many Requests"},
    },
)
@limiter.limit("10/minute")
async def patch_account(
    request: Request,
    payload: AccountProfilePatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    display_name = payload.display_name.strip()
    pieces = display_name.split()
    first_name = pieces[0]
    last_name = " ".join(pieces[1:])
    changed_fields = []
    if user.first_name != first_name:
        user.first_name = first_name
        changed_fields.append("first_name")
    if user.last_name != last_name:
        user.last_name = last_name
        changed_fields.append("last_name")
    if user.phone_number != payload.phone:
        user.phone_number = payload.phone
        changed_fields.append("phone_number")
    if changed_fields:
        db.add(AuditLog(
            user_id=user.id,
            action="PROFILE_UPDATED",
            table_name="users",
            record_id=str(user.id),
            new_value={"fields": sorted(changed_fields)},
            ip_address=request.client.host if request.client else None,
        ))
        await db.commit()
    return await get_account(db, user)


async def _password_change_failure_counts(
    db: AsyncSession,
    user_id: UUID,
    ip_address: str | None,
    since: datetime,
) -> tuple[int, int]:
    user_failures = (await db.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.user_id == user_id,
            AuditLog.action == PASSWORD_CHANGE_FAILED_ACTION,
            AuditLog.created_at >= since,
        )
    )).scalar_one()
    ip_failures = 0
    if ip_address:
        ip_failures = (await db.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.ip_address == ip_address,
                AuditLog.action == PASSWORD_CHANGE_FAILED_ACTION,
                AuditLog.created_at >= since,
            )
        )).scalar_one()
    return user_failures, ip_failures


def _ensure_password_change_allowed(user_failures: int, ip_failures: int) -> None:
    if user_failures >= 5 or ip_failures >= 5:
        raise HTTPException(
            status_code=429,
            detail="Too many failed password attempts. Try again in 15 minutes.",
        )


async def _load_password_change_session(
    request: Request,
    user: User,
    db: AsyncSession,
    now: datetime,
) -> tuple[AuthSession, UUID, bool]:
    from app.modules.auth.router import _request_session_id

    current_session_id = _request_session_id(request, user)
    current_session = await db.get(AuthSession, current_session_id)
    if (
        not current_session
        or current_session.revoked_at is not None
        or current_session.expires_at <= now
    ):
        raise HTTPException(status_code=401, detail=INVALID_SESSION_DETAIL)
    return current_session, current_session_id, bool(current_session.remember_me)


async def _verify_password_change_authorization(
    payload: AccountPasswordChange,
    user: User,
    db: AsyncSession,
    ip_address: str | None,
    current_session_id: UUID,
    user_failures: int,
    ip_failures: int,
) -> None:
    from app.modules.auth.router import _recent_authentication

    if user.local_password_enabled:
        if (
            not payload.current_password
            or not user.hashed_password
            or not await run_in_threadpool(
                security.verify_password,
                payload.current_password,
                user.hashed_password,
            )
        ):
            db.add(AuditLog(
                user_id=user.id,
                action=PASSWORD_CHANGE_FAILED_ACTION,
                table_name="users",
                record_id=str(user.id),
                new_value={"reason": "incorrect_current_password"},
                ip_address=ip_address,
            ))
            await db.commit()
            if user_failures >= 4 or ip_failures >= 4:
                raise HTTPException(
                    status_code=429,
                    detail="Too many failed password attempts. Try again in 15 minutes.",
                )
            raise HTTPException(
                status_code=400,
                detail="Unable to change password. Check your current password and try again.",
            )
        if payload.new_password == payload.current_password:
            raise HTTPException(
                status_code=400,
                detail="Choose a password different from your current password.",
            )
    elif not await _recent_authentication(db, user.id, current_session_id):
        raise HTTPException(
            status_code=403,
            detail="Reauthenticate with a linked provider before setting a password.",
        )


def _validate_new_password(user: User, payload: AccountPasswordChange) -> None:
    from app.core.password_policy import COMMON_WEAK_PASSWORDS

    new_password = payload.new_password
    if not 10 <= len(new_password) <= 128:
        raise HTTPException(status_code=400, detail="New password must be 10 to 128 characters long.")
    if new_password.casefold() in COMMON_WEAK_PASSWORDS:
        raise HTTPException(status_code=400, detail="Choose a password that is not commonly used.")
    display_name = f"{user.first_name} {user.last_name}".strip().casefold()
    email_local_part = user.email.partition("@")[0].casefold()
    folded_password = new_password.casefold()
    if any(identity and identity in folded_password for identity in (display_name, email_local_part)):
        raise HTTPException(status_code=400, detail="New password must not contain your name or email.")
    if new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="New password confirmation does not match.")


async def _change_existing_password(
    request: Request,
    payload: AccountPasswordChange,
    user: User,
    db: AsyncSession,
) -> dict:
    from app.modules.auth.router import self_change_password
    from app.modules.auth.schemas import ChangePasswordRequest

    request.state.account_password_policy = True
    return await self_change_password(
        request,
        ChangePasswordRequest(
            old_password=payload.current_password or "",
            new_password=payload.new_password,
            confirm_new_password=payload.confirm_password,
        ),
        user,
        db,
    )


async def _set_initial_password(
    user: User,
    db: AsyncSession,
    new_password: str,
    now: datetime,
    ip_address: str | None,
    current_session_id: UUID,
) -> dict:
    hashed_password = await run_in_threadpool(security.get_password_hash, new_password)
    user.hashed_password = hashed_password
    user.local_password_enabled = True
    user.must_change_password = False
    user.password_changed_at = now
    db.add(PasswordHistory(user_id=user.id, hashed_password=hashed_password))
    revoked_sessions = await db.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user.id,
            AuthSession.id != current_session_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    db.add(AuditLog(
        user_id=user.id,
        action="PASSWORD_CHANGED",
        table_name="users",
        record_id=str(user.id),
        new_value={
            "other_sessions_revoked": max(revoked_sessions.rowcount or 0, 0),
            "password_set": True,
        },
        ip_address=ip_address,
    ))
    await db.commit()
    return {"message": "Password set successfully."}


async def _rotate_password_session_and_notify(
    request: Request,
    response: Response,
    user: User,
    db: AsyncSession,
    current_session: AuthSession,
    ip_address: str | None,
    remember_me: bool,
) -> None:
    from app.modules.auth.router import _set_auth_cookies
    from app.modules.auth.service import AuthService

    current_session.revoked_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    rotated = await AuthService.issue_authenticated_session(
        db,
        user,
        ip_address or UNKNOWN_CLIENT,
        request.headers.get("user-agent", UNKNOWN_CLIENT),
        remember_me=remember_me,
        audit_action=PASSWORD_SESSION_ROTATED_ACTION,
    )
    _set_auth_cookies(response, request, rotated, remember_me)
    from app.services.email_service import EmailMessage, email_service

    display_name = f"{user.first_name} {user.last_name}".strip() or "there"
    await email_service.send(EmailMessage(
        to=[user.email],
        subject="Your password was changed",
        text_body=(
            f"Hello {display_name},\n\n"
            "Your KPI Management System password was changed. "
            "If you did not make this change, contact your system administrator."
        ),
    ))


@account_router.post(
    "/me/password",
    responses={
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        422: {"description": "Unprocessable Entity"},
        429: {"description": "Too Many Requests"},
    },
)
@limiter.limit("5/minute")
async def change_account_password(
    request: Request,
    response: Response,
    payload: AccountPasswordChange,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    now = datetime.now(UTC).replace(tzinfo=None)
    ip_address = request.client.host if request.client else None
    user_failures, ip_failures = await _password_change_failure_counts(
        db,
        user.id,
        ip_address,
        now - PASSWORD_CHANGE_FAILURE_WINDOW,
    )
    _ensure_password_change_allowed(user_failures, ip_failures)
    current_session, current_session_id, remember_me = await _load_password_change_session(
        request,
        user,
        db,
        now,
    )
    await _verify_password_change_authorization(
        payload,
        user,
        db,
        ip_address,
        current_session_id,
        user_failures,
        ip_failures,
    )
    _validate_new_password(user, payload)
    if user.local_password_enabled:
        result = await _change_existing_password(request, payload, user, db)
    else:
        result = await _set_initial_password(
            user,
            db,
            payload.new_password,
            now,
            ip_address,
            current_session_id,
        )
    await _rotate_password_session_and_notify(
        request,
        response,
        user,
        db,
        current_session,
        ip_address,
        remember_me,
    )
    return result


@account_router.post(
    "/me/reauth",
    responses={
        400: {"description": "Bad Request"},
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
        422: {"description": "Unprocessable Entity"},
        429: {"description": "Too Many Requests"},
    },
)
@limiter.limit("5/minute")
async def reauthenticate_account(
    request: Request,
    payload: AccountReauthentication,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not user.local_password_enabled:
        raise HTTPException(
            status_code=400,
            detail="Passwordless accounts must reauthenticate with a linked OAuth provider.",
        )
    if not user.hashed_password or not await run_in_threadpool(
        security.verify_password, payload.current_password, user.hashed_password
    ):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    token = request.headers.get("authorization", "")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    else:
        token = request.cookies.get("access_token", "").removeprefix("Bearer ").strip()
    try:
        claims = security.decode_token(token, "access", require_session=True)
        session_id = UUID(claims["sid"])
        if UUID(claims["user_id"]) != user.id:
            raise ValueError("Session user mismatch.")
    except (security.JWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail=INVALID_SESSION_DETAIL) from None

    now = datetime.now(UTC).replace(tzinfo=None)
    result = await db.execute(
        update(AuthSession)
        .where(
            AuthSession.id == session_id,
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
        .values(last_used_at=now, last_reauthenticated_at=now)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=401, detail=INVALID_SESSION_DETAIL)
    await db.commit()
    return {
        "status": "reauthenticated",
        "valid_for_seconds": REAUTHENTICATION_VALID_FOR_SECONDS,
    }

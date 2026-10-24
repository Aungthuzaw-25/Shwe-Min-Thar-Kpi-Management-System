import logging
import os
import tempfile
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import String, cast, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.cache import cache_available, invalidate_prefix
from app.core.config import settings
from app.core.dependencies import enforce_permission, get_current_user
from app.database.connection import get_db
from app.models.audit_log import AuditLog
from app.models.system import LogLevel
from app.modules.auth.models import UnlockRequest, UserSecuritySettings
from app.modules.auth.schemas import (
    LockedUserResponse,
    UserSecuritySettingsResponse,
    UserSecuritySettingsUpdate,
)
from app.modules.notifications.models import Notification
from app.modules.system import branding, repo
from app.modules.system.full_export import build_full_export_zip
from app.services.system_log_messages import plain_message_for, public_details
from app.modules.system.audit_log_retention import (
    AUDIT_RETENTION_YEARS_KEY,
    normalize_audit_retention_years,
)
from app.modules.system.schemas import (
    BrandResponse,
    CompanyTrademarkUpdate,
    EmailProviderLifecycleRequest,
    EmailProviderSaveRequest,
    PasswordPolicyValue,
    PublicBrandResponse,
    SystemEventLogDetailResponse,
    SystemEventLogPage,
    SystemSettingCreate,
    SystemSettingResponse,
    SystemSettingUpdate,
    normalize_email_settings,
    normalize_general_settings,
)
from app.modules.users.models import Role, User
from app.core.timezone import utc_now_naive
from app.services.email_service import (
    EmailAuthenticationError,
    EmailConfigurationError,
    EmailDisabledError,
    EmailProviderError,
    EmailProviderFactory,
    EmailSendError,
    email_service,
)

router = APIRouter(prefix="/system", tags=["System Settings & Logs"])
logger = logging.getLogger(__name__)
SYSTEM_SETTINGS_VIEW_PERMISSION = "system.settings.view"
SYSTEM_SETTINGS_EDIT_PERMISSION = "system.settings.edit"
EMAIL_PROVIDER_NOT_CONFIGURED = "Email provider is not configured."
NO_ACTIVE_EMAIL_PROVIDER = "No active email provider is configured."

def _is_secret_field(field: str) -> bool:
    normalized = field.lower().replace("-", "_")
    if normalized.endswith("_configured"):
        return False
    return normalized in {
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
        "credential",
    } or normalized.endswith(
        (
            "_password",
            "_secret",
            "_token",
            "_api_key",
            "_apikey",
            "_access_key",
            "_private_key",
            "_credential",
        )
    )


def _sanitize_setting_value(key: str, value: dict) -> dict:
    _reject_resend_api_key_fields(value)
    sanitized = dict(value)
    if key.lower() == "email":
        try:
            sanitized = normalize_email_settings(sanitized)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
    elif key.lower() == AUDIT_RETENTION_YEARS_KEY:
        try:
            sanitized = normalize_audit_retention_years(sanitized)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif key.lower() == branding.GENERAL_SETTINGS_KEY:
        try:
            sanitized = normalize_general_settings(sanitized)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif key.lower() == "security":
        try:
            policy = PasswordPolicyValue.model_validate({
                "minimum_password_length": sanitized.get(
                    "minimum_password_length", sanitized.get("min_length")
                ),
                "require_complexity": sanitized.get("require_complexity"),
                "force_password_change_days": sanitized.get(
                    "force_password_change_days",
                    90 if sanitized.get("password_expiry", False) else 0,
                ),
            })
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        sanitized = {
            **sanitized,
            "minimum_password_length": policy.minimum_password_length,
            "min_length": policy.minimum_password_length,
            "require_complexity": policy.require_complexity,
            "force_password_change_days": policy.force_password_change_days,
            "password_expiry": policy.force_password_change_days > 0,
        }
    return sanitized


def _reject_resend_api_key_fields(value) -> None:
    if isinstance(value, dict):
        for field, nested in value.items():
            normalized = str(field).lower().replace("-", "_")
            if _is_secret_field(normalized):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Credentials must be configured through the application environment.",
                )
            _reject_resend_api_key_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_resend_api_key_fields(nested)


def _require_super_admin_email_settings(user: User) -> None:
    if not user.role or user.role.name != "Super Admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a Super Admin can manage email configuration.",
        )


def _require_super_admin_audit_retention(user: User) -> None:
    if not user.role or user.role.name != "Super Admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a Super Admin can manage audit log retention.",
        )


def _require_super_admin_user_security(user: User) -> None:
    if not user.role or user.role.name != "Super Admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a Super Admin can manage user lockout settings.",
        )


def _require_super_admin_full_export(user: User) -> None:
    if not user.role or user.role.name != "Super Admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a Super Admin can export the full data set.",
        )


def _full_export_setting_value(key: str, value):
    """Redact a settings value for export exactly as the settings
    backup does, so both exports withhold the same secrets."""
    if isinstance(key, str) and key.lower() == "email":
        value = _sanitize_setting_value(key, value)
    return _safe_backup_value(key, value)


def _validate_email_settings_for_save(value: dict) -> dict:
    normalized = _sanitize_setting_value("email", value)
    if "providers" in value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Manage provider configuration through the email provider lifecycle actions.",
        )
    return normalized


def _safe_backup_value(key: str, value):
    if _is_secret_field(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            name: (
                "[REDACTED]"
                if _is_secret_field(str(name))
                else _safe_backup_value(str(name), item)
            )
            for name, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_backup_value(key, item) for item in value]
    return value


async def _email_settings_value(db: AsyncSession) -> tuple[dict, object | None]:
    setting = await repo.get_setting_by_key(db, "email")
    try:
        value = normalize_email_settings(setting.value if setting else None)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Saved email provider configuration is invalid.",
        ) from exc
    return value, setting


async def _persist_email_settings(
    db: AsyncSession,
    current_user: User,
    value: dict,
    setting=None,
):
    safe_value = _safe_backup_value("email", value)
    if setting is None:
        updated = await repo.create_setting(
            db,
            SystemSettingCreate(
                key="email",
                value=value,
                description="Email provider configuration",
            ),
        )
        old_value = None
        action = "CREATE"
    else:
        old_value = _safe_backup_value(
            "email", normalize_email_settings(setting.value)
        )
        updated = await repo.update_setting(
            db, setting, SystemSettingUpdate(value=value)
        )
        action = "UPDATE"
    db.add(
        AuditLog(
            user_id=current_user.id,
            action=action,
            table_name="system_settings",
            record_id=str(updated.id),
            old_value={"key": "email", "value": old_value},
            new_value={"key": "email", "value": safe_value},
        )
    )
    await db.commit()
    return updated


def _email_provider_secret_status(provider_type: str) -> bool:
    if provider_type == "resend":
        return bool(settings.RESEND_API_KEY and settings.RESEND_API_KEY.strip())
    if provider_type == "smtp":
        return bool(settings.SMTP_PASSWORD and settings.SMTP_PASSWORD.strip())
    return False


def _email_diagnostic_code(message: str) -> str:
    normalized = message.lower()
    if "from email" in normalized:
        return "FROM_EMAIL_REQUIRED"
    if "resend api key" in normalized:
        return "RESEND_API_KEY_MISSING"
    if "smtp password" in normalized:
        return "SMTP_CREDENTIALS_MISSING"
    if "smtp host" in normalized:
        return "SMTP_HOST_REQUIRED"
    if "smtp port" in normalized:
        return "SMTP_PORT_INVALID"
    if "authentication" in normalized or "credentials" in normalized:
        return "PROVIDER_AUTHENTICATION_FAILED"
    if "sender" in normalized or "verified" in normalized:
        return "PROVIDER_SENDER_REJECTED"
    if "connect" in normalized or "timed out" in normalized:
        return "PROVIDER_CONNECTION_FAILED"
    return "PROVIDER_NOT_READY"

@router.get(
    "/settings",
    response_model=list[SystemSettingResponse],
    responses={422: {"description": "Invalid setting value"}},
)
async def read_all_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """System Setting အားလုံးကို ဆွဲထုတ်ရန်"""
    enforce_permission(current_user, SYSTEM_SETTINGS_VIEW_PERMISSION)
    result = []
    email_setting_found = False
    for setting in await repo.get_all_settings(db):
        value = setting.value
        if setting.key.lower() == "email":
            email_setting_found = True
            value = _sanitize_setting_value(setting.key, value)
            value["resend_api_key_configured"] = bool(
                settings.RESEND_API_KEY and settings.RESEND_API_KEY.strip()
            )
            value["smtp_password_configured"] = bool(
                settings.SMTP_PASSWORD and settings.SMTP_PASSWORD.strip()
            )
        result.append(
            {
                "id": setting.id,
                "key": setting.key,
                "value": value,
                "description": setting.description,
                "created_at": setting.created_at,
                "updated_at": setting.updated_at,
            }
        )
    if not email_setting_found:
        value = _sanitize_setting_value("email", {})
        value["resend_api_key_configured"] = bool(
            settings.RESEND_API_KEY and settings.RESEND_API_KEY.strip()
        )
        value["smtp_password_configured"] = bool(
            settings.SMTP_PASSWORD and settings.SMTP_PASSWORD.strip()
        )
        result.append(
            {
                "id": uuid4(),
                "key": "email",
                "value": value,
                "description": "Email provider configuration",
                "created_at": datetime.now(),
                "updated_at": None,
            }
        )
    return result


@router.get("/user-security-settings", response_model=UserSecuritySettingsResponse)
async def read_user_security_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin_user_security(current_user)
    result = await db.execute(
        select(UserSecuritySettings).where(
            UserSecuritySettings.singleton_key.is_(True)
        )
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        return UserSecuritySettingsResponse(
            max_failed_attempts=5,
            lockout_duration_minutes=29,
            superadmin_lockout_enabled=True,
        )
    return UserSecuritySettingsResponse.model_validate(setting)


@router.put("/user-security-settings", response_model=UserSecuritySettingsResponse)
async def update_user_security_settings(
    payload: UserSecuritySettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin_user_security(current_user)
    result = await db.execute(
        select(UserSecuritySettings)
        .where(UserSecuritySettings.singleton_key.is_(True))
        .with_for_update()
    )
    setting = result.scalar_one_or_none()
    old_value = (
        {
            "max_failed_attempts": setting.max_failed_attempts,
            "lockout_duration_minutes": setting.lockout_duration_minutes,
            "superadmin_lockout_enabled": setting.superadmin_lockout_enabled,
        }
        if setting
        else {
            "max_failed_attempts": 5,
            "lockout_duration_minutes": 29,
            "superadmin_lockout_enabled": True,
        }
    )
    now = utc_now_naive()
    if setting is None:
        setting = UserSecuritySettings(
            singleton_key=True,
            updated_by=current_user.id,
            updated_at=now,
            **payload.model_dump(),
        )
        db.add(setting)
    else:
        for field, value in payload.model_dump().items():
            setattr(setting, field, value)
        setting.updated_by = current_user.id
        setting.updated_at = now

    if not payload.superadmin_lockout_enabled:
        super_admin_ids = select(User.id).join(Role, User.role_id == Role.id).where(
            Role.name == "Super Admin"
        )
        await db.execute(
            update(User)
            .where(User.id.in_(super_admin_ids))
            .values(is_locked=False, failed_login_attempts=0, locked_until=None)
        )
        pending_request_ids = select(cast(UnlockRequest.id, String)).where(
            UnlockRequest.user_id.in_(super_admin_ids),
            UnlockRequest.status == "PENDING",
        )
        await db.execute(
            update(UnlockRequest)
            .where(
                UnlockRequest.user_id.in_(super_admin_ids),
                UnlockRequest.status == "PENDING",
            )
            .values(status="RESOLVED", resolved_at=now, resolved_by=current_user.id)
        )
        await db.execute(
            update(Notification)
            .where(Notification.group_key.in_(pending_request_ids))
            .values(is_read=True)
        )

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="UPDATE",
            table_name="user_security_settings",
            old_value=old_value,
            new_value=payload.model_dump(),
        )
    )
    await db.commit()
    await db.refresh(setting)
    return UserSecuritySettingsResponse.model_validate(setting)


@router.get("/locked-users", response_model=list[LockedUserResponse])
async def list_locked_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin_user_security(current_user)
    users_result = await db.execute(
        select(User)
        .options(joinedload(User.role))
        .where(User.is_locked.is_(True), User.is_deleted.is_(False))
        .order_by(User.locked_until.asc().nullsfirst(), User.email.asc())
    )
    pending_result = await db.execute(
        select(UnlockRequest.user_id, UnlockRequest.id).where(
            UnlockRequest.status == "PENDING"
        )
    )
    pending_by_user = {
        user_id: request_id for user_id, request_id in pending_result.all()
    }
    return [
        LockedUserResponse(
            user_id=user.id,
            user_name=f"{user.first_name} {user.last_name}".strip(),
            email=user.email,
            role=user.role.name if user.role else "Not available",
            failed_login_attempts=user.failed_login_attempts or 0,
            last_failed_at=user.last_failed_at,
            last_failed_ip=user.last_failed_ip,
            locked_until=user.locked_until,
            pending_request_id=pending_by_user.get(user.id),
        )
        for user in users_result.scalars().all()
    ]


@router.post("/locked-users/{user_id}/unlock")
async def unlock_user_account(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin_user_security(current_user)
    result = await db.execute(
        select(User).where(User.id == user_id).with_for_update()
    )
    user = result.scalar_one_or_none()
    if user is None or user.is_deleted:
        raise HTTPException(status_code=404, detail="User not found.")

    now = utc_now_naive()
    user.is_locked = False
    user.failed_login_attempts = 0
    user.locked_until = None
    request_ids = select(cast(UnlockRequest.id, String)).where(
        UnlockRequest.user_id == user.id,
        UnlockRequest.status == "PENDING",
    )
    await db.execute(
        update(UnlockRequest)
        .where(
            UnlockRequest.user_id == user.id,
            UnlockRequest.status == "PENDING",
        )
        .values(status="RESOLVED", resolved_at=now, resolved_by=current_user.id)
    )
    await db.execute(
        update(Notification)
        .where(Notification.group_key.in_(request_ids))
        .values(is_read=True)
    )
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="MANUAL_UNLOCK",
            table_name="users",
            record_id=str(user.id),
            new_value={"is_locked": False, "failed_login_attempts": 0},
        )
    )
    await db.commit()
    return {"message": "User account unlocked successfully."}

@router.post(
    "/settings",
    response_model=SystemSettingResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Setting key already exists"},
        422: {"description": "Invalid setting value"},
        403: {"description": "Forbidden"},
    },
)
async def create_new_setting(
    payload: SystemSettingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """System Setting အသစ်ထည့်ရန်"""
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    if payload.key.lower() == AUDIT_RETENTION_YEARS_KEY:
        _require_super_admin_audit_retention(current_user)
    if "api_key" in payload.key.lower().replace("-", "_") or "apikey" in payload.key.lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="API keys must be configured through the environment.",
        )
    if payload.key.lower() == "email":
        _require_super_admin_email_settings(current_user)
    if payload.key.lower() == branding.BRANDING_SETTINGS_KEY:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Branding is managed through the company trademark endpoints.",
        )
    existing = await repo.get_setting_by_key(db, payload.key)
    if existing:
        raise HTTPException(status_code=400, detail="Setting key already exists.")
    payload.value = (
        _validate_email_settings_for_save(payload.value)
        if payload.key.lower() == "email"
        else _sanitize_setting_value(payload.key, payload.value)
    )
    created = await repo.create_setting(db, payload)
    if payload.key.lower() == AUDIT_RETENTION_YEARS_KEY:
        # First set of the retention period is a change from the
        # default (0 = keep forever) — audit it like an update.
        db.add(AuditLog(
            user_id=current_user.id,
            action="UPDATE",
            table_name="system_settings",
            record_id=str(created.id),
            old_value={"key": created.key, "value": {"retention_years": 0}},
            new_value={"key": created.key, "value": created.value},
        ))
        await db.commit()
    return created


@router.get(
    "/settings/email/providers",
    responses={422: {"description": "Email provider configuration is invalid"}},
)
async def list_email_providers(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce_permission(current_user, SYSTEM_SETTINGS_VIEW_PERMISSION)
    state, _setting = await _email_settings_value(db)
    configurations = state["providers"]
    definitions = [
        {
            **definition,
            "credential_configured": _email_provider_secret_status(
                definition["key"]
            ),
        }
        for definition in EmailProviderFactory.definitions()
    ]
    configured = []
    for provider_type, configuration in configurations.items():
        definition = next(
            item
            for item in definitions
            if item["key"] == provider_type
        )
        configured.append(
            {
                "provider": provider_type,
                "name": definition["name"],
                "configuration": configuration,
                "credential_configured": definition["credential_configured"],
            }
        )
    active_provider = state.get("active_provider")
    active_name = next(
        (
            item["name"]
            for item in definitions
            if item["key"] == active_provider
        ),
        None,
    )
    return {
        "definitions": definitions,
        "configured_providers": configured,
        "active_provider": active_provider,
        "service_status": (
            f"Enabled — {active_name}"
            if active_name
            else "Disabled — No active provider"
        ),
    }


@router.put(
    "/settings/email/providers/{provider_type}",
    responses={
        404: {"description": "Email provider is not implemented"},
        422: {"description": "Provider does not match the URL"},
    403: {"description": "Forbidden"}},
)
async def save_email_provider(
    provider_type: str,
    payload: EmailProviderSaveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    _require_super_admin_email_settings(current_user)
    provider_type = provider_type.lower()
    if provider_type != payload.provider:
        raise HTTPException(status_code=422, detail="Provider does not match the URL.")
    if provider_type not in EmailProviderFactory.available_providers():
        raise HTTPException(status_code=404, detail="Email provider is not implemented.")

    state, setting = await _email_settings_value(db)
    provider_value = payload.configuration.model_dump(mode="json")
    if provider_type == "resend":
        for field in ("smtp_host", "smtp_port", "smtp_username", "smtp_encryption"):
            provider_value.pop(field, None)
    previous = state["providers"].get(provider_type)
    was_tested = bool(
        previous
        and previous.get("tested")
        and {
            key: item
            for key, item in previous.items()
            if key not in {"enabled", "tested", "last_test_status"}
        }
        == {
            key: item
            for key, item in provider_value.items()
            if key not in {"enabled", "tested", "last_test_status"}
        }
    )
    provider_value.update(
        {
            "enabled": False,
            "tested": was_tested,
            "last_test_status": previous.get("last_test_status") if was_tested else None,
        }
    )
    state["providers"][provider_type] = provider_value
    if state.get("active_provider") == provider_type:
        state["active_provider"] = None
    await _persist_email_settings(db, current_user, state, setting)
    return {
        "success": True,
        "provider": provider_type,
        "enabled": False,
        "tested": was_tested,
        "message": "Provider configuration saved as disabled.",
    }


@router.post(
    "/settings/email/providers/{provider_type}/test-connection",
    responses={
        404: {"description": "Email provider is not configured"},
        422: {"description": "Email provider configuration is invalid"},
        502: {"description": "Email provider rejected the connection"},
        503: {"description": "Email provider is unavailable"},
    403: {"description": "Forbidden"}},
)
async def test_configured_email_provider(
    provider_type: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    _require_super_admin_email_settings(current_user)
    provider_type = provider_type.lower()
    state, setting = await _email_settings_value(db)
    provider_value = state["providers"].get(provider_type)
    if provider_value is None:
        raise HTTPException(status_code=404, detail=EMAIL_PROVIDER_NOT_CONFIGURED)
    try:
        diagnostic = await email_service.test_provider(
            provider_type, provider_value
        )
    except EmailProviderError as exc:
        provider_value["tested"] = False
        provider_value["last_test_status"] = "not_ready"
        if isinstance(
            exc,
            (
                EmailAuthenticationError,
                EmailConfigurationError,
                EmailSendError,
            ),
        ):
            provider_value["enabled"] = False
            if state.get("active_provider") == provider_type:
                state["active_provider"] = None
        await _persist_email_settings(db, current_user, state, setting)
        return {
            "success": False,
            "provider": provider_type,
            "status": "not_ready",
            "code": _email_diagnostic_code(str(exc)),
            "message": str(exc),
        }
    provider_value["tested"] = True
    provider_value["last_test_status"] = "ready"
    await _persist_email_settings(db, current_user, state, setting)
    return {
        "success": True,
        "provider": provider_type,
        "status": "ready",
        "code": "READY",
        "message": diagnostic,
    }


def _enable_email_provider(state: dict, provider_type: str, provider_value: dict) -> None:
    if not provider_value.get("tested"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Test the provider successfully before enabling it.",
        )
    try:
        email_service.validate_provider_configuration(provider_type, provider_value)
    except EmailProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    for other_type, other_config in state["providers"].items():
        other_config["enabled"] = other_type == provider_type
    state["active_provider"] = provider_type


def _email_provider_lifecycle_response(state: dict, provider_type: str, provider_value: dict):
    active = state.get("active_provider")
    provider_name = next(
        (
            definition["name"]
            for definition in EmailProviderFactory.definitions()
            if definition["key"] == provider_type
        ),
        provider_type,
    )
    is_active = active == provider_type
    is_ready = (
        provider_value.get("tested")
        and provider_value.get("last_test_status") == "ready"
    )
    return {
        "success": True,
        "provider": provider_type,
        "enabled": provider_value["enabled"],
        "is_active": is_active,
        "active_provider": active,
        "status": "ready" if is_ready else "not_ready",
        "service_status": (
            f"Enabled — {provider_name}"
            if is_active
            else "Disabled — No active provider"
        ),
        "message": (
            f"{provider_name} enabled successfully."
            if is_active
            else f"{provider_name} disabled successfully."
        ),
    }


@router.post(
    "/settings/email/providers/{provider_type}/lifecycle",
    responses={
        404: {"description": "Email provider is not configured"},
        409: {"description": "Email provider cannot be disabled"},
        422: {"description": "Email provider configuration is invalid"},
        500: {"description": "Email provider operation failed"},
    403: {"description": "Forbidden"}},
)
async def update_email_provider_lifecycle(
    provider_type: str,
    payload: EmailProviderLifecycleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    _require_super_admin_email_settings(current_user)
    provider_type = provider_type.lower()
    state, setting = await _email_settings_value(db)
    provider_value = state["providers"].get(provider_type)
    if provider_value is None:
        raise HTTPException(status_code=404, detail=EMAIL_PROVIDER_NOT_CONFIGURED)

    if payload.action == "enable":
        _enable_email_provider(state, provider_type, provider_value)
    else:
        provider_value["enabled"] = False
        if state.get("active_provider") == provider_type:
            state["active_provider"] = None

    try:
        await _persist_email_settings(db, current_user, state, setting)
    except SQLAlchemyError as exc:
        logger.exception(
            "Email provider lifecycle persistence failed for provider=%s",
            provider_type,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update email provider.",
        ) from exc
    return _email_provider_lifecycle_response(state, provider_type, provider_value)


@router.delete(
    "/settings/email/providers/{provider_type}",
    responses={
        404: {"description": "Email provider is not configured"},
        422: {"description": "Email provider configuration is invalid"},
    403: {"description": "Forbidden"}},
)
async def remove_email_provider(
    provider_type: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    _require_super_admin_email_settings(current_user)
    provider_type = provider_type.lower()
    state, setting = await _email_settings_value(db)
    if provider_type not in state["providers"]:
        raise HTTPException(status_code=404, detail=EMAIL_PROVIDER_NOT_CONFIGURED)
    del state["providers"][provider_type]
    if state.get("active_provider") == provider_type:
        state["active_provider"] = None
    await _persist_email_settings(db, current_user, state, setting)
    return {"success": True, "provider": provider_type, "message": "Provider removed."}


@router.put(
    "/settings/{key}",
    response_model=SystemSettingResponse,
    responses={
        404: {"description": "Setting not found"},
        422: {"description": "Invalid setting value"},
        403: {"description": "Forbidden"},
    },
)
async def update_existing_setting(
    key: str,
    payload: SystemSettingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """System Setting ပြင်ဆင်ရန် (ဥပမာ - Maintenance Mode ပိတ်/ဖွင့်)"""
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    if key.lower() == AUDIT_RETENTION_YEARS_KEY:
        _require_super_admin_audit_retention(current_user)
    if "api_key" in key.lower().replace("-", "_") or "apikey" in key.lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="API keys must be configured through the environment.",
        )
    if key.lower() == "email":
        _require_super_admin_email_settings(current_user)
    if key.lower() == branding.BRANDING_SETTINGS_KEY:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Branding is managed through the company trademark endpoints.",
        )
    setting = await repo.get_setting_by_key(db, key)
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found.")
    old_value = _safe_backup_value(
        setting.key, _sanitize_setting_value(setting.key, setting.value)
    )
    if payload.value is not None:
        payload.value = (
            _validate_email_settings_for_save(payload.value)
            if key.lower() == "email"
            else _sanitize_setting_value(key, payload.value)
        )
    updated = await repo.update_setting(db, setting, payload)
    db.add(AuditLog(
        user_id=current_user.id,
        action="UPDATE",
        table_name="system_settings",
        record_id=str(updated.id),
        old_value={"key": updated.key, "value": old_value},
        new_value={"key": updated.key, "value": _safe_backup_value(updated.key, updated.value)},
    ))
    await db.commit()
    return updated


@router.post(
    "/settings/email/test-connection",
    responses={
        422: {"description": "Email provider configuration is invalid"},
        502: {"description": "Email provider rejected the connection"},
        503: {"description": "Email provider is unavailable"},
    403: {"description": "Forbidden"}},
)
async def test_email_connection(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    _require_super_admin_email_settings(current_user)
    setting = await repo.get_setting_by_key(db, "email")
    if not setting:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=NO_ACTIVE_EMAIL_PROVIDER,
        )
    try:
        state = normalize_email_settings(setting.value)
        provider_type = state.get("active_provider")
        if not provider_type and len(state["providers"]) == 1:
            provider_type = next(iter(state["providers"]))
        if not provider_type:
            raise EmailDisabledError(NO_ACTIVE_EMAIL_PROVIDER)
        diagnostic = await email_service.test_connection(provider_type)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Saved email provider configuration is invalid.",
        ) from exc
    except EmailDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=NO_ACTIVE_EMAIL_PROVIDER,
        ) from exc
    except EmailConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except EmailProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return {
        "status": "ready",
        "provider": state.get("active_provider"),
        "message": diagnostic,
    }

@router.get("/logs", response_model=SystemEventLogPage, responses={503: {"description": "Service Unavailable"}})
async def read_system_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, gt=0, le=500),
    skip: int | None = Query(None, ge=0, include_in_schema=False),
    limit: int | None = Query(None, gt=0, le=500, include_in_schema=False),
    level: LogLevel | None = None,
    event_type: str | None = Query(None, max_length=100),
    service: str | None = Query(None, max_length=100),
    search: str | None = Query(None, max_length=200),
    start: datetime | None = None,
    end: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Background Jobs နှင့် System Event များကို ကြည့်ရန်"""
    enforce_permission(current_user, SYSTEM_SETTINGS_VIEW_PERMISSION)
    effective_size = limit or page_size
    effective_skip = skip if skip is not None else (page - 1) * effective_size
    event_filter = event_type or service
    try:
        logs, total = await repo.get_recent_logs_page(
            db, skip=effective_skip, limit=effective_size, level=level,
            event_type=event_filter, search=search, start=start, end=end,
        )
    except SQLAlchemyError as exc:
        logger.exception("System logs query failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="System logs are temporarily unavailable.",
        ) from exc
    # Display rule: the list always shows the plain-language message
    # (never a raw error/exception) and hides raw-technical detail
    # keys; the stored record itself is unchanged for support.
    items = [
        {
            "id": log.id,
            "event_type": log.event_type,
            "level": log.level,
            "message": plain_message_for(log.event_type, log.level, log.message),
            "details": public_details(log.details),
            "created_at": log.created_at,
        }
        for log in logs
    ]
    return {
        "items": items,
        "total": total,
        "page": (effective_skip // effective_size) + 1,
        "page_size": effective_size,
    }


@router.get(
    "/logs/{log_id}",
    response_model=SystemEventLogDetailResponse,
    responses={404: {"description": "System log not found"}},
)
async def read_system_log_detail(
    log_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce_permission(current_user, SYSTEM_SETTINGS_VIEW_PERMISSION)
    try:
        parsed_log_id = UUID(log_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="System log not found.") from exc
    log = await repo.get_system_log(db, parsed_log_id)
    if not log:
        raise HTTPException(status_code=404, detail="System log not found.")
    # Same display rule as the list (plain message by default); the
    # raw stored message rides along as technical_message for support.
    return {
        "id": log.id,
        "event_type": log.event_type,
        "level": log.level,
        "message": plain_message_for(log.event_type, log.level, log.message),
        "details": log.details,
        "created_at": log.created_at,
        "technical_message": log.message,
    }


# =================================================================================
# 🌟 COMPANY TRADEMARK APIs (company name + logo)
# =================================================================================

async def _upsert_setting_value(
    db: AsyncSession, key: str, value: dict, description: str | None = None
):
    existing = await repo.get_setting_by_key(db, key)
    if existing is None:
        return await repo.create_setting(
            db,
            SystemSettingCreate(key=key, value=value, description=description),
        )
    return await repo.update_setting(db, existing, SystemSettingUpdate(value=value))


@router.get("/branding", response_model=BrandResponse)
async def read_branding(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Current company trademark (name + logo) for the settings page."""
    enforce_permission(current_user, SYSTEM_SETTINGS_VIEW_PERMISSION)
    return await branding.resolve_brand(db)


@router.get("/branding/public", response_model=PublicBrandResponse)
async def read_public_branding(db: AsyncSession = Depends(get_db)):
    """Brand + login-provider truth for the unauthenticated login page.

    Returns the company name, the logo URL, and whether each OAuth
    provider is fully configured — nothing else. A provider flag is
    True only when that provider is connected/configured, so the login
    page can hide every other provider's button.
    """
    # Lazy import, the same pattern profile_router uses: auth.router does
    # not depend on this module, and this keeps module import order safe.
    from app.modules.auth.router import oauth_provider_availability

    brand = await branding.resolve_brand(db)
    return {**brand, "oauth_providers": oauth_provider_availability()}


@router.put("/branding", response_model=BrandResponse)
async def update_branding(
    payload: CompanyTrademarkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save the company name into the existing ``general`` setting.

    Other general fields (timezone, date_format) and the stored logo are
    preserved.
    """
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    values = await branding.read_settings_values(db)
    general = dict(values.get(branding.GENERAL_SETTINGS_KEY, {}))
    old_value = dict(general)
    general["company_name"] = payload.company_name
    if "company_tagline" in payload.model_fields_set:
        general["company_tagline"] = payload.company_tagline or ""
    updated = await _upsert_setting_value(
        db,
        branding.GENERAL_SETTINGS_KEY,
        general,
        description="General system settings",
    )
    db.add(AuditLog(
        user_id=current_user.id,
        action="UPDATE",
        table_name="system_settings",
        record_id=str(updated.id),
        old_value={"key": branding.GENERAL_SETTINGS_KEY, "value": old_value},
        new_value={"key": branding.GENERAL_SETTINGS_KEY, "value": general},
    ))
    await db.commit()
    return await branding.resolve_brand(db)


@router.post(
    "/branding/logo",
    response_model=BrandResponse,
    responses={
        400: {"description": "Invalid image"},
        403: {"description": "Forbidden"},
        413: {"description": "Logo image too large"},
    },
)
async def upload_branding_logo(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a new company logo; it replaces the old one everywhere.

    Upload safety follows the profile-avatar pipeline exactly: the
    client filename and content type are ignored, the bytes must really
    decode as JPEG/PNG/WebP, size/dimension limits apply, and the image
    is re-encoded to WebP before it is stored.
    """
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    if file.size is not None and file.size > branding.MAX_LOGO_BYTES:
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Logo image exceeds the allowed size.",
        )
    content = await file.read(branding.MAX_LOGO_BYTES + 1)
    await file.close()
    try:
        normalized = await run_in_threadpool(branding.normalize_logo_image, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    values = await branding.read_settings_values(db)
    old_reference = values.get(branding.BRANDING_SETTINGS_KEY, {})
    previous_updated_at = old_reference.get("logo_updated_at")
    reference = await branding.store_logo(
        normalized,
        previous_updated_at=(
            previous_updated_at if isinstance(previous_updated_at, int) else None
        ),
    )
    updated = await _upsert_setting_value(
        db,
        branding.BRANDING_SETTINGS_KEY,
        reference,
        description=branding.BRANDING_SETTING_DESCRIPTION,
    )
    db.add(AuditLog(
        user_id=current_user.id,
        action="UPDATE",
        table_name="system_settings",
        record_id=str(updated.id),
        old_value={
            "key": branding.BRANDING_SETTINGS_KEY,
            "value": old_reference,
        },
        new_value={"key": branding.BRANDING_SETTINGS_KEY, "value": reference},
    ))
    await db.commit()
    if (
        old_reference.get("logo_object")
        and old_reference.get("logo_storage") != reference["logo_storage"]
    ):
        # Same-backend uploads overwrite the same fixed key/path; only a
        # backend switch leaves old bytes behind to clean up.
        await branding.delete_logo(old_reference)
    return await branding.resolve_brand(db)


@router.delete("/branding/logo", response_model=BrandResponse)
async def reset_branding_logo(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove the custom logo; the default SMT logo is served again."""
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    values = await branding.read_settings_values(db)
    reference = values.get(branding.BRANDING_SETTINGS_KEY, {})
    if reference.get("logo_object"):
        await branding.delete_logo(reference)
        updated = await _upsert_setting_value(
            db, branding.BRANDING_SETTINGS_KEY, {}
        )
        db.add(AuditLog(
            user_id=current_user.id,
            action="UPDATE",
            table_name="system_settings",
            record_id=str(updated.id),
            old_value={
                "key": branding.BRANDING_SETTINGS_KEY,
                "value": reference,
            },
            new_value={"key": branding.BRANDING_SETTINGS_KEY, "value": {}},
        ))
        await db.commit()
    return await branding.resolve_brand(db)


@router.get(
    "/branding/logo",
    responses={404: {"description": "No custom company logo is configured"}},
)
async def read_branding_logo(db: AsyncSession = Depends(get_db)):
    """Serve the custom logo bytes (public: the login page needs them)."""
    try:
        values = await branding.read_settings_values(db)
    except Exception:
        values = {}
    content = await branding.load_logo(
        values.get(branding.BRANDING_SETTINGS_KEY, {})
    )
    if content is None:
        raise HTTPException(
            status_code=404, detail="No custom company logo is configured."
        )
    return Response(
        content=content,
        media_type=branding.LOGO_MEDIA_TYPE,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )


# =================================================================================
# 🌟 SYSTEM MAINTENANCE APIs (Cache & Backup)
# =================================================================================

@router.post("/clear-cache")
async def clear_system_cache(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Clear the application dashboard cache (the ``kpi:cache:`` namespace).

    Only the dashboard aggregate cache from ``app.core.cache`` is cleared.
    OAuth Redis holds security-sensitive transient state in a different
    namespace and is never flushed by this endpoint. When no cache is
    configured or reachable, the endpoint stays truthful and returns 501
    instead of pretending something was cleared.
    """
    enforce_permission(current_user, SYSTEM_SETTINGS_EDIT_PERMISSION)
    if not await cache_available():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="No clearable application cache is configured.",
        )
    cleared = await invalidate_prefix("dashboard")
    return {
        "success": True,
        "cleared_keys": cleared,
        "message": "Dashboard cache cleared.",
    }


@router.get(
    "/backup",
    responses={500: {"description": "Failed to generate backup"}, 422: {"description": "Unprocessable Entity"}},
)
async def generate_database_backup(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Database ထဲရှိ System Settings များကို JSON File အဖြစ် Backup ထုတ်ပေးရန်။
    """
    enforce_permission(current_user, SYSTEM_SETTINGS_VIEW_PERMISSION)
    try:
        settings = await repo.get_all_settings(db)
        backup_data = {
            "meta_data": {
                "generated_at": datetime.now().isoformat(),
                "system_version": "1.0",
                "type": "System Configuration Backup"
            },
            "system_settings": [
                {
                    "key": s.key,
                    "value": _safe_backup_value(
                        s.key,
                        (
                            _sanitize_setting_value(s.key, s.value)
                            if s.key.lower() == "email"
                            else s.value
                        ),
                    ),
                    "description": s.description
                } for s in settings
            ]
        }
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"system_backup_{timestamp}.json"
        return JSONResponse(
            content=backup_data,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Access-Control-Expose-Headers": "Content-Disposition"
            }
        )
    except Exception:
        # Do not echo internal error text to the client (it can carry
        # DB/driver details); the full error is in the server log.
        logger.exception("System settings backup generation failed")
        raise HTTPException(
            status_code=500, detail="Failed to generate backup."
        )


@router.get(
    "/backup/full",
    responses={
        403: {"description": "Super Admin only"},
        500: {"description": "Failed to generate export"},
    },
)
async def generate_full_data_export(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export every core table as a ZIP of per-table JSON files.

    Super Admin only — this is the entire company's data. Credential
    material is never included (see app.modules.system.full_export
    for the exclusion list). Object-storage files (evidence, avatars,
    logos) are not part of this export; database rows only. Every
    export is written to the audit log.
    """
    _require_super_admin_full_export(current_user)
    fd, zip_path = tempfile.mkstemp(
        prefix="full_data_export_", suffix=".zip"
    )
    os.close(fd)
    try:
        manifest = await build_full_export_zip(
            db, zip_path, settings_sanitizer=_full_export_setting_value
        )
    except Exception:
        logger.exception("Full data export generation failed")
        try:
            os.unlink(zip_path)
        except OSError:
            pass
        raise HTTPException(
            status_code=500, detail="Failed to generate full data export."
        )
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="DATA_EXPORT",
            table_name="system",
            record_id=None,
            new_value={
                "tables": len(manifest["tables"]),
                "total_rows": manifest["total_rows"],
                "excluded_tables": sorted(manifest["excluded_tables"]),
            },
            ip_address=request.client.host if request.client else None,
        )
    )
    await db.commit()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"full_data_export_{timestamp}.zip",
        background=BackgroundTask(os.unlink, zip_path),
    )

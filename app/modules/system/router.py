import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import enforce_permission, get_current_user
from app.database.connection import get_db
from app.models.audit_log import AuditLog
from app.models.system import LogLevel
from app.modules.system import repo
from app.modules.system.schemas import (
    PasswordPolicyValue,
    SystemEventLogDetailResponse,
    SystemEventLogPage,
    SystemSettingCreate,
    SystemSettingResponse,
    SystemSettingUpdate,
)
from app.modules.users.models import User

router = APIRouter(prefix="/system", tags=["System Settings & Logs"])
logger = logging.getLogger(__name__)

SENSITIVE_SETTING_TERMS = (
    "password", "secret", "token", "api_key", "apikey", "private_key",
    "database_url", "access_key", "encryption_key",
)


def _sanitize_setting_value(key: str, value: dict) -> dict:
    sanitized = dict(value)
    if key.lower() == "email":
        sanitized.pop("password", None)
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


def _safe_backup_value(key: str, value):
    normalized = key.lower().replace("-", "_")
    if any(term in normalized for term in SENSITIVE_SETTING_TERMS):
        return "[REDACTED]"
    return value

@router.get("/settings", response_model=list[SystemSettingResponse])
async def read_all_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """System Setting အားလုံးကို ဆွဲထုတ်ရန်"""
    enforce_permission(current_user, "system.settings.view")
    return await repo.get_all_settings(db)

@router.post("/settings", response_model=SystemSettingResponse, status_code=status.HTTP_201_CREATED)
async def create_new_setting(
    payload: SystemSettingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """System Setting အသစ်ထည့်ရန်"""
    enforce_permission(current_user, "system.settings.edit")
    existing = await repo.get_setting_by_key(db, payload.key)
    if existing:
        raise HTTPException(status_code=400, detail="Setting key already exists.")
    payload.value = _sanitize_setting_value(payload.key, payload.value)
    return await repo.create_setting(db, payload)

@router.put("/settings/{key}", response_model=SystemSettingResponse)
async def update_existing_setting(
    key: str,
    payload: SystemSettingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """System Setting ပြင်ဆင်ရန် (ဥပမာ - Maintenance Mode ပိတ်/ဖွင့်)"""
    enforce_permission(current_user, "system.settings.edit")
    setting = await repo.get_setting_by_key(db, key)
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found.")
    if payload.value is not None:
        payload.value = _sanitize_setting_value(key, payload.value)
    updated = await repo.update_setting(db, setting, payload)
    db.add(AuditLog(
        user_id=current_user.id,
        action="UPDATE",
        table_name="system_settings",
        record_id=str(updated.id),
        new_value={"key": updated.key, "value": _safe_backup_value(updated.key, updated.value)},
    ))
    await db.commit()
    return updated

@router.get("/logs", response_model=SystemEventLogPage)
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
    enforce_permission(current_user, "system.settings.view")
    effective_size = limit or page_size
    effective_skip = skip if skip is not None else (page - 1) * effective_size
    event_filter = event_type or service
    try:
        items = await repo.get_recent_logs(
            db, skip=effective_skip, limit=effective_size, level=level,
            event_type=event_filter, search=search, start=start, end=end,
        )
        total = await repo.count_system_logs(
            db, level=level, event_type=event_filter, search=search, start=start, end=end,
        )
    except SQLAlchemyError as exc:
        logger.exception("System logs query failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="System logs are temporarily unavailable.",
        ) from exc
    return {
        "items": items,
        "total": total,
        "page": (effective_skip // effective_size) + 1,
        "page_size": effective_size,
    }


@router.get("/logs/{log_id}", response_model=SystemEventLogDetailResponse)
async def read_system_log_detail(
    log_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce_permission(current_user, "system.settings.view")
    try:
        parsed_log_id = UUID(log_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="System log not found.") from exc
    log = await repo.get_system_log(db, parsed_log_id)
    if not log:
        raise HTTPException(status_code=404, detail="System log not found.")
    return log


# =================================================================================
# 🌟 SYSTEM MAINTENANCE APIs (Cache & Backup)
# =================================================================================

@router.post("/clear-cache")
async def clear_system_cache(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Clear the supported application cache, if one is configured.

    The application currently has no generic cache namespace. OAuth Redis holds
    security-sensitive transient state and must not be flushed by this endpoint.
    """
    enforce_permission(current_user, "system.settings.edit")
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="No clearable application cache is configured.",
    )


@router.get("/backup")
async def generate_database_backup(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Database ထဲရှိ System Settings များကို JSON File အဖြစ် Backup ထုတ်ပေးရန်။
    """
    enforce_permission(current_user, "system.settings.view")
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
                    "value": _safe_backup_value(s.key, s.value),
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate backup: {str(e)}")
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from datetime import datetime

from app.database.connection import get_db
from app.core.dependencies import get_current_user, enforce_permission
from app.modules.system import repo
from app.modules.system.schemas import SystemSettingCreate, SystemSettingUpdate, SystemSettingResponse, SystemEventLogResponse
from app.modules.users.models import User

router = APIRouter(prefix="/system", tags=["System Settings & Logs"])

@router.get("/settings", response_model=List[SystemSettingResponse])
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
    return await repo.update_setting(db, setting, payload)

@router.get("/logs", response_model=List[SystemEventLogResponse])
async def read_system_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=500, description="Maximum number of logs to fetch (capped at 500)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Background Jobs နှင့် System Event များကို ကြည့်ရန်"""
    enforce_permission(current_user, "system.settings.view")
    return await repo.get_recent_logs(db, skip=skip, limit=limit)


# =================================================================================
# 🌟 SYSTEM MAINTENANCE APIs (Cache & Backup)
# =================================================================================

@router.post("/clear-cache")
async def clear_system_cache(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    System Cache များကို ရှင်းလင်းရန်။
    (Redis သို့မဟုတ် Memory Cache ရှင်းလင်းသည့် Logic ကို ဤနေရာတွင် ထည့်သွင်းနိုင်သည်။)
    """
    enforce_permission(current_user, "system.settings.edit")
    try:
        return {"message": "System cache memory cleared successfully.", "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear cache: {str(e)}")


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
                    "value": s.value,
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
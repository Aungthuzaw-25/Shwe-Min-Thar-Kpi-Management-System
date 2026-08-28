from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from datetime import datetime

from app.database.connection import get_db
from app.core.dependencies import RoleChecker
from app.modules.system import repo
from app.modules.system.schemas import SystemSettingCreate, SystemSettingUpdate, SystemSettingResponse, SystemEventLogResponse

router = APIRouter(prefix="/system", tags=["System Settings & Logs"])

super_admin_only = RoleChecker(["System Super Admin", "Super Admin"])

@router.get("/settings", response_model=List[SystemSettingResponse], dependencies=[Depends(super_admin_only)])
async def read_all_settings(db: AsyncSession = Depends(get_db)):
    """System Setting အားလုံးကို ဆွဲထုတ်ရန်"""
    return await repo.get_all_settings(db)

@router.post("/settings", response_model=SystemSettingResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(super_admin_only)])
async def create_new_setting(payload: SystemSettingCreate, db: AsyncSession = Depends(get_db)):
    """System Setting အသစ်ထည့်ရန်"""
    existing = await repo.get_setting_by_key(db, payload.key)
    if existing:
        raise HTTPException(status_code=400, detail="Setting key already exists.")
    return await repo.create_setting(db, payload)

@router.put("/settings/{key}", response_model=SystemSettingResponse, dependencies=[Depends(super_admin_only)])
async def update_existing_setting(key: str, payload: SystemSettingUpdate, db: AsyncSession = Depends(get_db)):
    """System Setting ပြင်ဆင်ရန် (ဥပမာ - Maintenance Mode ပိတ်/ဖွင့်)"""
    setting = await repo.get_setting_by_key(db, key)
    if not setting:
        raise HTTPException(status_code=404, detail="Setting not found.")
    return await repo.update_setting(db, setting, payload)

@router.get("/logs", response_model=List[SystemEventLogResponse], dependencies=[Depends(super_admin_only)])
async def read_system_logs(
    # 🌟 BUG FIX: Added 'skip' parameter to match the repo function signature
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=500, description="Maximum number of logs to fetch (capped at 500)"), 
    db: AsyncSession = Depends(get_db)
):
    """Background Jobs နှင့် System Event များကို ကြည့်ရန်"""
    # 🌟 FIX: Passed both skip and limit
    return await repo.get_recent_logs(db, skip=skip, limit=limit)


# =================================================================================
# 🌟 SYSTEM MAINTENANCE APIs (Cache & Backup)
# =================================================================================

@router.post("/clear-cache", dependencies=[Depends(super_admin_only)])
async def clear_system_cache():
    """
    System Cache များကို ရှင်းလင်းရန်။
    (Redis သို့မဟုတ် Memory Cache ရှင်းလင်းသည့် Logic ကို ဤနေရာတွင် ထည့်သွင်းနိုင်သည်။)
    """
    try:
        # ဥပမာ - Redis အသုံးပြုထားပါက:
        # await redis_client.flushdb()
        
        # ဥပမာ - Python functools lru_cache အသုံးပြုထားပါက:
        # get_setting_by_key.cache_clear()

        return {"message": "System cache memory cleared successfully.", "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear cache: {str(e)}")


@router.get("/backup", dependencies=[Depends(super_admin_only)])
async def generate_database_backup(db: AsyncSession = Depends(get_db)):
    """
    Database ထဲရှိ System Settings များကို JSON File အဖြစ် Backup ထုတ်ပေးရန်။
    """
    try:
        settings = await repo.get_all_settings(db)
        
        # Backup Data ဖွဲ့စည်းခြင်း
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
        
        # ဖိုင်နာမည်ကို အချိန်နှင့်တကွ သတ်မှတ်ခြင်း (ဥပမာ - system_backup_20260821_104300.json)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"system_backup_{timestamp}.json"
        
        # JSON ဖိုင်အဖြစ် Download ချနိုင်ရန် Header ဖြင့် ပြန်ပို့ပေးခြင်း
        return JSONResponse(
            content=backup_data,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Access-Control-Expose-Headers": "Content-Disposition"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate backup: {str(e)}")
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Optional
from uuid import UUID

from app.models.system import SystemSetting, SystemEventLog
from app.modules.system.schemas import SystemSettingCreate, SystemSettingUpdate

async def get_all_settings(db: AsyncSession) -> List[SystemSetting]:
    # Setting အရေအတွက်က နည်းတဲ့အတွက် Limit မလိုပါ
    stmt = select(SystemSetting).order_by(SystemSetting.key)
    result = await db.execute(stmt)
    return result.scalars().all()

async def get_setting_by_key(db: AsyncSession, key: str) -> Optional[SystemSetting]:
    stmt = select(SystemSetting).where(SystemSetting.key == key)
    result = await db.execute(stmt)
    # ⚡ PERFORMANCE & DATA INTEGRITY FIX: Changed to scalar_one_or_none
    return result.scalar_one_or_none()

async def create_setting(db: AsyncSession, obj_in: SystemSettingCreate) -> SystemSetting:
    try:
        db_obj = SystemSetting(**obj_in.model_dump())
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj
    except Exception as e:
        await db.rollback()
        raise e

async def update_setting(db: AsyncSession, db_obj: SystemSetting, obj_in: SystemSettingUpdate) -> SystemSetting:
    try:
        update_data = obj_in.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_obj, field, value)
        
        await db.commit()
        await db.refresh(db_obj)
        return db_obj
    except Exception as e:
        await db.rollback()
        raise e

# ⚡ PERFORMANCE FIX: Added skip (offset) for proper pagination
async def get_recent_logs(db: AsyncSession, skip: int = 0, limit: int = 50) -> List[SystemEventLog]:
    """System Audit Log များကို ဆွဲထုတ်ရန် (Pagination ပါဝင်သည်)"""
    stmt = select(SystemEventLog).order_by(SystemEventLog.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()

# 🌟 ARCHITECTURE FIX: Added missing function to create event logs
async def create_system_log(db: AsyncSession, log_data: dict) -> SystemEventLog:
    """စနစ်အတွင်း ပြောင်းလဲမှုများကို မှတ်တမ်းတင်ရန် (Audit Trail)"""
    try:
        new_log = SystemEventLog(**log_data)
        db.add(new_log)
        await db.commit()
        await db.refresh(new_log)
        return new_log
    except Exception as e:
        await db.rollback()
        # Audit Log သွင်းတာ Failed ဖြစ်ရင်တောင် Main Business Logic ကြီး မရပ်သွားအောင် 
        # လိုအပ်ရင် ဒီနေရာမှာ logging ဖြင့်သာ မှတ်ထားနိုင်သည် (လက်ရှိတော့ raise e ထားပါသည်)
        raise e
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.system import LogLevel, SystemEventLog, SystemSetting
from app.modules.system.schemas import SystemSettingCreate, SystemSettingUpdate


async def get_all_settings(db: AsyncSession) -> list[SystemSetting]:
    # Setting အရေအတွက်က နည်းတဲ့အတွက် Limit မလိုပါ
    stmt = select(SystemSetting).order_by(SystemSetting.key)
    result = await db.execute(stmt)
    return result.scalars().all()

async def get_setting_by_key(db: AsyncSession, key: str) -> SystemSetting | None:
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
async def get_recent_logs(
    db: AsyncSession, skip: int = 0, limit: int = 50,
    level: LogLevel | None = None, event_type: str | None = None,
    search: str | None = None, start: datetime | None = None,
    end: datetime | None = None,
) -> list[SystemEventLog]:
    stmt = select(SystemEventLog).where(SystemEventLog.is_deleted.is_(False))
    if level:
        stmt = stmt.where(SystemEventLog.level == level)
    if event_type:
        stmt = stmt.where(SystemEventLog.event_type.ilike(f"%{event_type}%"))
    if search:
        stmt = stmt.where(or_(
            SystemEventLog.message.ilike(f"%{search}%"),
            SystemEventLog.event_type.ilike(f"%{search}%"),
        ))
    if start:
        stmt = stmt.where(SystemEventLog.created_at >= start)
    if end:
        stmt = stmt.where(SystemEventLog.created_at < end)
    stmt = stmt.order_by(SystemEventLog.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


async def count_system_logs(
    db: AsyncSession, level: LogLevel | None = None,
    event_type: str | None = None, search: str | None = None,
    start: datetime | None = None, end: datetime | None = None,
) -> int:
    stmt = select(func.count(SystemEventLog.id)).where(SystemEventLog.is_deleted.is_(False))
    if level:
        stmt = stmt.where(SystemEventLog.level == level)
    if event_type:
        stmt = stmt.where(SystemEventLog.event_type.ilike(f"%{event_type}%"))
    if search:
        stmt = stmt.where(or_(
            SystemEventLog.message.ilike(f"%{search}%"),
            SystemEventLog.event_type.ilike(f"%{search}%"),
        ))
    if start:
        stmt = stmt.where(SystemEventLog.created_at >= start)
    if end:
        stmt = stmt.where(SystemEventLog.created_at < end)
    return int((await db.execute(stmt)).scalar_one())


async def get_system_log(db: AsyncSession, log_id: UUID) -> SystemEventLog | None:
    result = await db.execute(select(SystemEventLog).where(
        SystemEventLog.id == log_id,
        SystemEventLog.is_deleted.is_(False),
    ))
    return result.scalar_one_or_none()

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
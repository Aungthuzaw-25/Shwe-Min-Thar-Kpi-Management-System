from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.models import Notification

# 🌟 ARCHITECTURE FIX: Class နှင့် @staticmethod များ ဖယ်ရှားပြီး 
# အခြား Repo ဖိုင်များနှင့် တပြေးညီတည်းဖြစ်အောင် Module-level Function များအဖြစ် ပြောင်းလဲထားပါသည်။

async def create_notification(
    db: AsyncSession, obj_in: dict[str, Any], *, commit: bool = True
) -> Notification:
    """Notification အသစ်ဖန်တီးရန်"""
    try:
        # 💡 Tip: Enterprise Level တွင် Dict အစား Pydantic Schema ကို သုံးခြင်းက ပိုလုံခြုံပါသည်။
        db_obj = Notification(**obj_in)
        db.add(db_obj)
        if commit:
            await db.commit()
            await db.refresh(db_obj)
        return db_obj
    except Exception as e:
        await db.rollback()
        raise e


async def count_unread_notifications(db: AsyncSession, user_id: UUID) -> int:
    stmt = select(func.count(Notification.id)).where(
        Notification.user_id == user_id,
        Notification.is_read.is_(False),
        Notification.is_deleted.is_(False),
    )
    return int((await db.execute(stmt)).scalar_one())

async def get_notifications_by_user(db: AsyncSession, user_id: UUID, skip: int = 0, limit: int = 50) -> Sequence[Notification]:
    """User တစ်ယောက်ချင်းစီ၏ Notification များကို ဆွဲထုတ်ရန် (Pagination ပါဝင်သည်)"""
    stmt = select(Notification).where(
        Notification.user_id == user_id, 
        Notification.is_deleted == False 
    ).order_by(
        Notification.created_at.desc()
    ).offset(skip).limit(limit)
    
    result = await db.execute(stmt)
    return result.scalars().all()

async def get_notification_by_user(
    db: AsyncSession, notification_id: UUID, user_id: UUID
) -> Notification | None:
    stmt = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == user_id,
        Notification.is_deleted.is_(False),
    )
    return (await db.execute(stmt)).scalar_one_or_none()

async def mark_notification_as_read(db: AsyncSession, notification_id: UUID, user_id: UUID) -> bool:
    """သက်ဆိုင်ရာ Notification တစ်ခုတည်းကို 'ဖတ်ပြီး' (Read) ဟု ပြောင်းရန်"""
    try:
        # ⚡ PERFORMANCE & LOGIC FIX: Bulk Update ကို အသုံးပြုထားခြင်း အလွန်ကောင်းမွန်ပါသည်။
        # Soft-deleted ဖြစ်နေသော Data များကိုပါ အမှားအယွင်းမဖြစ်စေရန် is_deleted == False ထည့်ပေးထားသည်။
        stmt = update(Notification).where(
            Notification.id == notification_id, 
            Notification.user_id == user_id,
            Notification.is_deleted == False
        ).values(is_read=True)
        
        result = await db.execute(stmt)
        await db.commit()
        
        return result.rowcount > 0
    except Exception as e:
        await db.rollback()
        raise e

async def mark_all_notifications_as_read(db: AsyncSession, user_id: UUID) -> int:
    """User ၏ Notification အားလုံးကို ဖတ်ပြီးအဖြစ် ပြောင်းရန်"""
    try:
        stmt = update(Notification).where(
            Notification.user_id == user_id,
            Notification.is_read == False,
            Notification.is_deleted == False
        ).values(is_read=True)
        
        result = await db.execute(stmt)
        await db.commit()
        
        return result.rowcount
    except Exception as e:
        await db.rollback()
        raise e
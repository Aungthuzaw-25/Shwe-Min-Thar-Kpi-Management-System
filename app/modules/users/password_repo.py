import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.security import verify_password_async

# 🌟 BUG FIX: PasswordHistory သည် ယခုအခါ auth.models တွင် ရှိပါသည်
from app.modules.auth.models import PasswordHistory


async def check_password_history(db: AsyncSession, user_id: uuid.UUID, raw_password: str) -> bool:
    # နောက်ဆုံးပြောင်းလဲခဲ့သော Password (၅) ခုကို ဆွဲထုတ်မည်
    stmt = select(PasswordHistory).where(PasswordHistory.user_id == user_id).order_by(PasswordHistory.created_at.desc()).limit(5)
    result = await db.execute(stmt)
    histories = result.scalars().all()
    
    # Password အဟောင်းများနှင့် တိုက်စစ်မည်
    for record in histories:
        if await verify_password_async(raw_password, record.hashed_password):
            return False # အဟောင်းနှင့် တူနေပါသည်
    return True

async def add_password_history(
    db: AsyncSession,
    user_id: uuid.UUID,
    hashed_password: str,
    *,
    commit: bool = True,
):
    new_history = PasswordHistory(user_id=user_id, hashed_password=hashed_password)
    db.add(new_history)
    if commit:
        await db.commit()
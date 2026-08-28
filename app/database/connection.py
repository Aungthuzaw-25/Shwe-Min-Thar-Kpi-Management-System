from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.core.config import settings

engine_options = {
    "echo": False,
    "pool_pre_ping": True,
}

if not settings.DATABASE_URL.startswith("sqlite"):
    engine_options.update(pool_recycle=300, pool_size=20, max_overflow=10)

engine = create_async_engine(settings.DATABASE_URL, **engine_options)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            # ❌ ဒီနေရာမှာ Auto-commit မလုပ်ရပါ။ 
            # Commit လုပ်ခြင်းကို Router (သို့) Repository အပိုင်းတွင်သာ သီးသန့် လုပ်ဆောင်ရပါမည်။
        finally:
            await session.close()
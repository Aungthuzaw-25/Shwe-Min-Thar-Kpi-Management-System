import logging
from urllib.parse import urlparse
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings

# Setup standard logger
logger = logging.getLogger(__name__)

# 🌟 FIX: Modern SQLAlchemy 2.0 Declarative Base for strict typing
class Base(DeclarativeBase):
    pass

# Base connection options
engine_options = {
    # Dynamically log SQL queries based on the environment (e.g., True in dev, False in prod)
    "echo": getattr(settings, "DEBUG", False),
    "pool_pre_ping": True,  # Tests connection liveness before checking out from pool
}

connect_args = {}
if settings.DATABASE_URL.startswith("postgresql+asyncpg://"):
    parsed = urlparse(settings.DATABASE_URL)
    host = (parsed.hostname or "").lower()
    is_localhost = host in {"localhost", "127.0.0.1", "::1"}
    if not is_localhost:
        connect_args["ssl"] = True

# 🌟 FIX: PostgreSQL/MySQL specific optimizations. SQLite doesn't support these pool args.
if not settings.DATABASE_URL.startswith("sqlite"):
    engine_options.update(
        pool_recycle=300,    # Reconnect after 5 minutes to prevent stale connections
        pool_size=20,        # Number of connections to keep open
        max_overflow=10,     # Allow up to 10 extra connections during spikes
        pool_timeout=30      # Prevent waiting forever if the pool is exhausted
    )

# Initialize the async engine
engine_kwargs = dict(engine_options)
if connect_args:
    engine_kwargs["connect_args"] = connect_args

engine = create_async_engine(
    settings.DATABASE_URL,
    **engine_kwargs,
)

# Configure the session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine, 
    class_=AsyncSession, 
    expire_on_commit=False,
    autoflush=False # 🌟 FIX: Prevents auto-query execution during uncommitted changes
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides an asynchronous database session.
    Handles explicit rollback if an HTTP exception or logic error occurs.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            # ❌ ဒီနေရာမှာ Auto-commit မလုပ်ရပါ။ 
            # Commit လုပ်ခြင်းကို Router (သို့) Repository အပိုင်းတွင်သာ သီးသန့် လုပ်ဆောင်ရပါမည်။
        except SQLAlchemyError as db_err:
            # 🌟 FIX: Explicitly rollback and log database-specific errors
            logger.error(f"Database error occurred: {str(db_err)}", exc_info=True)
            await session.rollback()
            raise
        except Exception as e:
            # 🌟 FIX: Explicitly rollback for any standard Python exceptions
            logger.error(f"Application error during DB session: {str(e)}", exc_info=True)
            await session.rollback()
            raise
        finally:
            # Guaranteed to return the connection back to the pool
            await session.close()
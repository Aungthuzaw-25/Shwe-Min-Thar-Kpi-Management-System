import logging
from collections.abc import AsyncGenerator
from urllib.parse import urlparse

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# Setup standard logger
logger = logging.getLogger(__name__)


# 🌟 FIX: Modern SQLAlchemy 2.0 Declarative Base for strict typing
class Base(DeclarativeBase):
    pass


# Base connection options
engine_options = {
    "echo": False,
    "pool_pre_ping": True,  # Tests connection liveness before checking out from pool
}

def _build_connect_args(database_url: str) -> dict:
    args: dict = {}
    if not database_url.startswith("postgresql+asyncpg://"):
        return args
    # Required when connecting through Neon's PgBouncer pooler, which
    # does not support prepared-statement caching across sessions.
    args["statement_cache_size"] = 0
    # Per-statement ceiling (asyncpg command_timeout): a single
    # statement is cancelled client-side after this long instead of
    # holding a pooled connection indefinitely. Client-side (rather
    # than a server SET) so it also holds through the PgBouncer
    # pooler. Long-running work such as bulk KPI calculation is many
    # short statements, so it is unaffected; only a genuinely stuck
    # statement is cut. Value: DB_COMMAND_TIMEOUT_SECONDS (default 60).
    args["command_timeout"] = settings.DB_COMMAND_TIMEOUT_SECONDS
    parsed = urlparse(database_url)
    host = (parsed.hostname or "").lower()
    is_localhost = host in {"localhost", "127.0.0.1", "::1"}
    if settings.DATABASE_SSL_REQUIRED and not is_localhost:
        args["ssl"] = True
    return args


connect_args = _build_connect_args(settings.DATABASE_URL)

# 🌟 FIX: PostgreSQL/MySQL specific optimizations. SQLite doesn't support these pool args.
if not settings.DATABASE_URL.startswith("sqlite"):
    engine_options.update(
        pool_recycle=1800,
        pool_size=10,
        max_overflow=10,
        pool_timeout=30,  # Prevent waiting forever if the pool is exhausted
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
    autoflush=False,  # 🌟 FIX: Prevents auto-query execution during uncommitted changes
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
            logger.exception("Database error occurred: %s", db_err)
            await session.rollback()
            raise
        except Exception as e:
            # 🌟 FIX: Explicitly rollback for any standard Python exceptions
            logger.exception("Application error during DB session: %s", e)
            await session.rollback()
            raise
        finally:
            # Guaranteed to return the connection back to the pool
            await session.close()

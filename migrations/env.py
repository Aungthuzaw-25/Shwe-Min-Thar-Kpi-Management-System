import asyncio
from logging.config import fileConfig
from urllib.parse import urlparse

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# --- Project ရဲ့ Settings နဲ့ Base ကို Import လုပ်ရန် ---
from app.core.config import settings
from app.database.base import Base

# 🌟 အရေးကြီးသည်: Tables များကို Alembic သိရှိစေရန် Model အားလုံးကို ဤနေရာတွင် Import လုပ်ပါ
from app.modules.auth.models import LoginHistory
from app.modules.auth.models import PasswordHistory
from app.modules.auth.models import TokenBlacklist
from app.modules.users.models import User, Position, Permission, RolePermission, Role, UserPermissionOverride
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.models import KPILevelSetting,KPIFormula,KPIRecord,KPIHistory,FormulaAuditLog,KPIPeriod,KPIAssignment,KPIScoringRuleItem,ActionPlan,KpiRule
from app.modules.kpi.models import KpiCategory  # added for Alembic metadata
from app.modules.notifications.models import Notification
from app.modules.targets.models import Target
from app.modules.tasks.models.activity_timeline import ActivityTimeline
from app.modules.tasks.models.approval_queue import ApprovalQueue
from app.modules.tasks.models.submission_batch import SubmissionBatch
from app.modules.tasks.models.task import Task
from app.modules.tasks.models.task_evidence import TaskEvidence
from app.modules.tasks.models.task_history import TaskHistory
from app.modules.tasks.models.task_review import TaskReview
from app.models.audit_log import AuditLog
from app.models.system import SystemSetting,SystemEventLog


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- Alembic ကို Project ရဲ့ Table တွေ အားလုံးသိစေရန် Base.metadata သို့ ချိတ်ဆက်ခြင်း ---
target_metadata = Base.metadata

def run_migrations_offline() -> None:
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    # --- .env ထဲက Database URL ကို သုံးရန် သတ်မှတ်ခြင်း ---
    configuration["sqlalchemy.url"] = settings.DATABASE_URL

    connect_args = {}
    database_url = urlparse(settings.DATABASE_URL)
    if (
        settings.DATABASE_URL.startswith("postgresql+asyncpg://")
        and database_url.hostname not in {"localhost", "127.0.0.1"}
    ):
        connect_args["ssl"] = True

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()

def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
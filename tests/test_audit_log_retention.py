import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.connection import Base
from app.models.audit_log import AuditLog
from app.models.system import SystemSetting
from app.modules.system.audit_log_retention import delete_expired_audit_logs


def test_audit_log_retention_deletes_only_records_older_than_cutoff(tmp_path):
    async def run():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'audit-log-retention.sqlite'}"
        )
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    lambda sync_connection: Base.metadata.create_all(
                        sync_connection,
                        tables=[AuditLog.__table__, SystemSetting.__table__],
                    )
                )
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                now = datetime.now(UTC).replace(tzinfo=None)
                db.add_all([
                    AuditLog(
                        id=uuid4(),
                        action="OLD",
                        created_at=now - timedelta(days=31),
                    ),
                    AuditLog(
                        id=uuid4(),
                        action="RECENT",
                        created_at=now - timedelta(days=29),
                    ),
                ])
                await db.commit()

                deleted = await delete_expired_audit_logs(
                    db, retention_days=30, batch_size=1
                )
                remaining = await db.scalar(select(func.count(AuditLog.id)))

            assert deleted == 1
            assert remaining == 1
        finally:
            await engine.dispose()

    asyncio.run(run())

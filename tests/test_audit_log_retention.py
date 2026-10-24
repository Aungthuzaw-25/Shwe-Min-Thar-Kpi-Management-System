"""audit_retention_years contract (Zenn, 2026-10-23):

- No setting, or 0 -> keep forever: the daily cleanup is a no-op.
- A finite period (1/2/5 years) deletes only rows older than it,
  in batches, and the purge itself leaves an AUDIT_LOG_PURGE row.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.connection import Base
from app.models.audit_log import AuditLog
from app.models.system import SystemSetting
from app.modules.system import audit_log_retention as retention_module


def _world(tmp_path, setting_value):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'audit-retention.sqlite'}"
    )
    return engine


def _run(tmp_path, monkeypatch, setting_value, ages_days, runs=1):
    async def run():
        engine = _world(tmp_path, setting_value)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    lambda sync_connection: Base.metadata.create_all(
                        sync_connection,
                        tables=[AuditLog.__table__, SystemSetting.__table__],
                    )
                )
            factory = async_sessionmaker(engine, expire_on_commit=False)
            monkeypatch.setattr(
                retention_module, "AsyncSessionLocal", factory
            )
            now = datetime.now(UTC).replace(tzinfo=None)
            async with factory() as db:
                if setting_value is not None:
                    db.add(SystemSetting(
                        key="audit_retention_years", value=setting_value
                    ))
                for age in ages_days:
                    db.add(AuditLog(
                        id=uuid4(), action=f"AGE_{age}",
                        created_at=now - timedelta(days=age),
                    ))
                await db.commit()
            deleted = 0
            for _ in range(runs):
                deleted += await retention_module.run_audit_log_retention_cleanup()
            async with factory() as db:
                remaining = (
                    await db.execute(
                        select(AuditLog.action).order_by(AuditLog.action)
                    )
                ).scalars().all()
                purge_rows = (
                    await db.execute(
                        select(AuditLog).where(
                            AuditLog.action == "AUDIT_LOG_PURGE"
                        )
                    )
                ).scalars().all()
            return deleted, remaining, purge_rows
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_no_setting_means_keep_forever(tmp_path, monkeypatch):
    deleted, remaining, purge_rows = _run(
        tmp_path, monkeypatch, None, [4000, 10]
    )
    assert deleted == 0
    assert remaining == ["AGE_10", "AGE_4000"]
    assert purge_rows == []


def test_zero_years_means_keep_forever(tmp_path, monkeypatch):
    deleted, remaining, purge_rows = _run(
        tmp_path, monkeypatch, {"retention_years": 0}, [4000, 10]
    )
    assert deleted == 0
    assert remaining == ["AGE_10", "AGE_4000"]
    assert purge_rows == []


def test_one_year_purges_only_older_rows_and_audits_itself(tmp_path, monkeypatch):
    deleted, remaining, purge_rows = _run(
        tmp_path, monkeypatch, {"retention_years": 1}, [800, 400, 100], runs=2
    )
    assert deleted == 2  # 800 and 400 days are beyond 1 year (365d)
    assert remaining == ["AGE_100", "AUDIT_LOG_PURGE"]
    assert len(purge_rows) == 1
    assert purge_rows[0].user_id is None
    assert purge_rows[0].new_value["retention_years"] == 1
    assert purge_rows[0].new_value["deleted_count"] == 2
    # (the helper ran the cleanup a second time: idempotent —
    # no extra deletions, no second purge row)

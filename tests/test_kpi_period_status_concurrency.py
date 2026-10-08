import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.dependencies import get_current_user
from app.database.connection import Base, get_db
from app.models.audit_log import AuditLog
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.models import KPIPeriod, KPIPeriodStatus
from app.modules.kpi.period_router import router as period_router
from app.modules.kpi.period_service import change_period_status
from app.modules.users.models import Permission, Position, Role, RolePermission, User

TABLES = [
    Department.__table__,
    Position.__table__,
    UserDepartment.__table__,
    Permission.__table__,
    Role.__table__,
    RolePermission.__table__,
    User.__table__,
    KPIPeriod.__table__,
    AuditLog.__table__,
]


async def _run_lifecycle_scenario(database_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection, tables=TABLES
                )
            )

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            edit_permission = Permission(code="kpi.edit")
            admin_role = Role(name="Admin", permissions=[edit_permission])
            super_admin_role = Role(name="Super Admin")
            admin = User(
                email=f"{uuid4()}@example.test",
                hashed_password=uuid4().hex,
                first_name="QA",
                last_name="Admin",
                role=admin_role,
                is_active=True,
                is_deleted=False,
            )
            super_admin = User(
                email=f"{uuid4()}@example.test",
                hashed_password=uuid4().hex,
                first_name="QA",
                last_name="Super Admin",
                role=super_admin_role,
                is_active=True,
                is_deleted=False,
            )
            period = KPIPeriod(
                label="Lifecycle concurrency test",
                start_date=datetime(2026, 10, 1, tzinfo=UTC),
                end_date=datetime(2026, 11, 1, tzinfo=UTC),
                status=KPIPeriodStatus.DRAFT,
                is_deleted=False,
            )
            db.add_all([admin, super_admin, period])
            await db.commit()
            period_id = period.id
            admin_actor = SimpleNamespace(
                id=admin.id,
                role=SimpleNamespace(
                    name="Admin",
                    permissions=[SimpleNamespace(code="kpi.edit")],
                ),
                permission_overrides=[],
            )
            super_admin_actor = SimpleNamespace(
                id=super_admin.id,
                role=SimpleNamespace(name="Super Admin", permissions=[]),
                permission_overrides=[],
            )
            hr_actor = SimpleNamespace(
                id=admin.id,
                role=SimpleNamespace(name="HR", permissions=[]),
                permission_overrides=[],
            )

        app = FastAPI()
        app.include_router(period_router, prefix="/api/v1/kpi")
        active_actor = [admin_actor]

        async def override_db():
            async with session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: active_actor[0]

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            for target in ("OPEN", "FINALIZED", "LOCKED"):
                response = await client.put(
                    f"/api/v1/kpi/periods/{period_id}/status",
                    params={"new_status": target},
                )
                assert response.status_code == 200
                assert response.json()["status"] == target

            repeated = await client.put(
                f"/api/v1/kpi/periods/{period_id}/status",
                params={"new_status": "LOCKED"},
            )
            assert repeated.status_code == 200
            active_actor[0] = hr_actor
            unauthorized = await client.put(
                f"/api/v1/kpi/periods/{period_id}/status",
                params={"new_status": "OPEN"},
            )
            assert unauthorized.status_code == 403
            active_actor[0] = admin_actor
            denied = await client.put(
                f"/api/v1/kpi/periods/{period_id}/status",
                params={"new_status": "OPEN", "reason": "Approved reopen"},
            )
            assert denied.status_code == 403

            active_actor[0] = super_admin_actor
            missing_reason = await client.put(
                f"/api/v1/kpi/periods/{period_id}/status",
                params={"new_status": "OPEN"},
            )
            assert missing_reason.status_code == 400

            reopened = await client.put(
                f"/api/v1/kpi/periods/{period_id}/status",
                params={"new_status": "OPEN", "reason": "Approved reopen"},
            )
            assert reopened.status_code == 200
            assert reopened.json()["status"] == "OPEN"

            invalid_enum = await client.put(
                f"/api/v1/kpi/periods/{period_id}/status",
                params={"new_status": "NOT_A_STATUS"},
            )
            assert invalid_enum.status_code == 422

        async with session_factory() as db:
            persisted = await db.get(KPIPeriod, period_id)
            assert persisted.status == KPIPeriodStatus.OPEN
            events = (
                await db.execute(
                    AuditLog.__table__.select().where(
                        AuditLog.table_name == "kpi_periods",
                        AuditLog.record_id == str(period_id),
                    )
                )
            ).mappings().all()
            assert [event["action"] for event in events] == [
                "PERIOD_STATUS_CHANGED",
                "PERIOD_STATUS_CHANGED",
                "PERIOD_STATUS_CHANGED",
                "REOPEN_PERIOD",
            ]
            assert events[-1]["old_value"] == {"status": "LOCKED"}
            assert events[-1]["new_value"] == {
                "status": "OPEN",
                "reason": "Approved reopen",
            }
    finally:
        await engine.dispose()


def test_period_lifecycle_preserves_transitions_authorization_and_audit(tmp_path):
    asyncio.run(_run_lifecycle_scenario(tmp_path / "period-lifecycle.sqlite"))


class _ZeroRowResult:
    rowcount = 0


class _StaleStatusSession:
    def __init__(self, period):
        self.period = period
        self.statements = []
        self.rolled_back = False
        self.added = []

    async def execute(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return SimpleNamespace(scalar_one_or_none=lambda: self.period)
        return _ZeroRowResult()

    async def rollback(self):
        self.rolled_back = True

    def add(self, value):
        self.added.append(value)


def test_stale_conditional_transition_returns_conflict_without_audit():
    period = SimpleNamespace(
        id=uuid4(),
        status=KPIPeriodStatus.OPEN,
        is_deleted=False,
    )
    db = _StaleStatusSession(period)

    async def run():
        with pytest.raises(HTTPException) as conflict:
            await change_period_status(
                db,
                period.id,
                KPIPeriodStatus.LOCKED,
                uuid4(),
                actor_role="Admin",
            )
        assert conflict.value.status_code == 409

    asyncio.run(run())
    assert db.rolled_back
    assert db.added == []
    where = str(db.statements[1].compile(compile_kwargs={"literal_binds": True}))
    assert "kpi_periods.status = 'OPEN'" in where

"""Over-achievement notices (Zenn, 2026-10-26): score stays capped,
SA + Admin get one DASHBOARD notice per period + employee +
category, and the record carries exceeded / exceeded_pct."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.connection import Base
from app.modules.kpi.engine_helpers import target_category_exceedances
from app.modules.kpi.models import PeriodType
from app.modules.kpi.overachievement import (
    NOTIFICATION_TYPE,
    build_overachievement_message,
    notify_overachievements,
)
from app.modules.notifications.models import Notification
from app.modules.users.models import Role, User

TABLES = [User.__table__, Role.__table__, Notification.__table__]


def _target_session(targets):
    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: targets)
            )

    return Session()


def _formula(categories):
    return SimpleNamespace(categories=categories)


def _target(name, value, metric_type):
    return SimpleNamespace(
        name=name, department_id="dept-1", target_value=value,
        metric_type=metric_type, status="ACTIVE", is_deleted=False,
    )


@pytest.mark.asyncio
async def test_exceedance_higher_reports_pct():
    entries = await target_category_exceedances(
        _target_session([_target("Sales", 100.0, "PERCENTAGE")]),
        _formula([{"name": "Sales", "evaluation_type": "TARGET"}]),
        "dept-1", "2026-10", {"sales": 125.0},
    )
    assert entries == [{
        "category": "Sales", "direction": "HIGHER",
        "target_value": 100.0, "actual_value": 125.0,
        "exceeded": True, "exceeded_pct": 25.0,
    }]


@pytest.mark.asyncio
async def test_exceedance_not_exceeded_and_non_computable():
    entries = await target_category_exceedances(
        _target_session([
            _target("Sales", 100.0, "PERCENTAGE"),
            _target("Defects", 10.0, "COUNT"),
            _target("Broken", 0.0, "COUNT"),
        ]),
        _formula([
            {"name": "Sales", "evaluation_type": "TARGET"},
            {"name": "Defects", "evaluation_type": "TARGET"},
            {"name": "Broken", "evaluation_type": "TARGET"},
            {"name": "Ghost", "evaluation_type": "TARGET"},
            {"name": "Late", "evaluation_type": "DEDUCTION"},
        ]),
        "dept-1", "2026-10", {"sales": 90.0, "defects": 10.0},
    )
    by_name = {entry["category"]: entry for entry in entries}
    # DEDUCTION categories never appear.
    assert "Late" not in by_name
    # Below target (HIGHER) / at target (LOWER): no excess.
    assert by_name["Sales"]["exceeded"] is False
    assert by_name["Sales"]["exceeded_pct"] is None
    assert by_name["Defects"]["exceeded"] is False
    assert by_name["Defects"]["exceeded_pct"] is None
    # Target <= 0 and missing target/actual: not computable.
    assert by_name["Broken"]["exceeded"] is False
    assert by_name["Broken"]["exceeded_pct"] is None
    assert by_name["Ghost"]["exceeded"] is False
    assert by_name["Ghost"]["exceeded_pct"] is None


@pytest.mark.asyncio
async def test_exceedance_lower_and_actual_zero_flag():
    entries = await target_category_exceedances(
        _target_session([_target("Defects", 10.0, "COUNT")]),
        _formula([{"name": "Defects", "evaluation_type": "TARGET"}]),
        "dept-1", "2026-10", {"defects": 0.0},
    )
    assert entries[0]["exceeded"] is True
    assert entries[0]["exceeded_pct"] == 100.0
    assert entries[0]["actual_zero"] is True


def _run_isolated_test(tmp_path, test_case):
    async def _with_db():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/over.sqlite")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    lambda sync_connection: Base.metadata.create_all(
                        sync_connection, tables=TABLES
                    )
                )
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                await test_case(db)
        finally:
            await engine.dispose()

    asyncio.run(_with_db())


async def _seed_recipients(db):
    roles = {}
    for name in ("Super Admin", "Admin", "HR"):
        role = Role(name=name)
        db.add(role)
        roles[name] = role
    await db.flush()
    users = {}
    for key, role_name, active, deleted in (
        ("sa", "Super Admin", True, False),
        ("sa_inactive", "Super Admin", False, False),
        ("sa_deleted", "Super Admin", True, True),
        ("admin", "Admin", True, False),
        ("hr", "HR", True, False),
    ):
        user = User(
            email=f"{key}-{uuid4()}@example.test",
            hashed_password="x",
            first_name=key, last_name="Test",
            role_id=roles[role_name].id,
            permission_overrides=[],
            is_active=active, is_deleted=deleted,
        )
        db.add(user)
        users[key] = user
    await db.commit()
    return users


def _exceeded_entry():
    return {
        "category": "Sales", "direction": "HIGHER",
        "target_value": 100.0, "actual_value": 125.0,
        "exceeded": True, "exceeded_pct": 25.0,
    }


async def _notify_with_recording(db, exceedances, employee=None):
    calls = []

    async def fake_notifier(**kwargs):
        calls.append(kwargs)
        db.add(Notification(
            user_id=kwargs["user_id"], title=kwargs["title"],
            message=kwargs["message"],
            notification_type=kwargs["notification_type"],
            channel=kwargs["channel"], group_key=kwargs["group_key"],
        ))
        await db.flush()

    if employee is None:
        employee = SimpleNamespace(
            id=uuid4(), first_name="Aung", last_name="Test",
        )
    sent = await notify_overachievements(
        db, employee_id=employee.id,
        employee_name=f"{employee.first_name} {employee.last_name}",
        period_type=PeriodType.MONTHLY,
        period_start=datetime(2026, 10, 1), exceedances=exceedances,
        notifier=fake_notifier,
    )
    return calls, sent


def test_notify_goes_to_active_sa_and_admin_only(tmp_path):
    async def case(db):
        await _seed_recipients(db)
        calls, sent = await _notify_with_recording(db, [_exceeded_entry()])
        assert len(calls) == 2
        assert len(sent) == 2
        assert {call["channel"] for call in calls} == {"DASHBOARD"}
        assert {call["notification_type"] for call in calls} == {NOTIFICATION_TYPE}
        assert all(call["group_key"] for call in calls)
        message = calls[0]["message"]
        assert "Aung Test" in message
        assert "Sales" in message
        assert "100" in message and "125" in message and "25" in message
        assert "Score ထဲမှာ ပိုတာ မပါဘူး" in message
        assert "Aung Test" in calls[0]["title"]

    _run_isolated_test(tmp_path, case)


def test_notify_dedups_second_finalize(tmp_path):
    async def case(db):
        await _seed_recipients(db)
        employee = SimpleNamespace(
            id=uuid4(), first_name="Aung", last_name="Test",
        )
        calls, _ = await _notify_with_recording(
            db, [_exceeded_entry()], employee=employee)
        assert len(calls) == 2
        # Same period + employee + category: nothing re-sent.
        calls2, sent2 = await _notify_with_recording(
            db, [_exceeded_entry()], employee=employee)
        assert calls2 == []
        assert sent2 == []

    _run_isolated_test(tmp_path, case)


def test_notify_sends_nothing_when_nothing_exceeded(tmp_path):
    async def case(db):
        await _seed_recipients(db)
        entry = _exceeded_entry()
        entry["exceeded"] = False
        entry["exceeded_pct"] = None
        calls, sent = await _notify_with_recording(db, [entry])
        assert calls == []
        assert sent == []

    _run_isolated_test(tmp_path, case)


def test_group_key_distinguishes_long_names_sharing_a_prefix():
    from app.modules.kpi.overachievement import overachievement_group_key

    employee_id = uuid4()
    key_a = overachievement_group_key(
        employee_id, PeriodType.MONTHLY, datetime(2026, 10, 1),
        "very long category name aaaaaaaaaa one",
    )
    key_b = overachievement_group_key(
        employee_id, PeriodType.MONTHLY, datetime(2026, 10, 1),
        "very long category name aaaaaaaaaa two",
    )
    assert key_a != key_b
    assert len(key_a) <= 100 and len(key_b) <= 100


def test_actual_zero_message_carries_the_flag():
    entry = _exceeded_entry()
    entry.update({"category": "Defects", "direction": "LOWER",
                  "target_value": 10.0, "actual_value": 0.0,
                  "exceeded_pct": 100.0, "actual_zero": True})
    message = build_overachievement_message("Aung Test", entry)
    assert "actual က 0" in message

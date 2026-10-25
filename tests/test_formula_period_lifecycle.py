"""Formula <-> period applicability + lifecycle (Zenn, 2026-10-24).

The single read rule (closed / current links / legacy month /
history-only), the split template-download errors, link
management (rows kept as history), manual close, revive (period
link mandatory, rules untouched), and the scheduler's warn +
auto-close behaviour.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.dependencies import get_current_user
from app.database.base import BaseModel
from app.database.connection import get_db
from app.models.audit_log import AuditLog
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.models import (
    FormulaScope,
    FormulaStatus,
    KPIAssignment,
    KPIFormula,
    KPIFormulaPeriod,
    KPIPeriod,
    KPIPeriodStatus,
    KPIScoringRuleItem,
    PeriodType,
)
from app.modules.kpi.period_ranges import calendar_period_range
from app.modules.kpi.upload_router import limiter
from app.modules.notifications.models import Notification
from app.modules.users.models import Position, Role, User


def _user(role, email, first="Test"):
    return User(
        email=email, hashed_password="x", first_name=first, last_name="User",
        role_id=role.id, role=role, is_active=True, is_deleted=False,
    )


def _monthly_period(label, year, month):
    rng = calendar_period_range(PeriodType.MONTHLY, date(year, month, 1))
    return KPIPeriod(
        label=label,
        start_date=rng.start_inclusive.astimezone(UTC).replace(tzinfo=None),
        end_date=rng.end_exclusive.astimezone(UTC).replace(tzinfo=None),
        status=KPIPeriodStatus.OPEN, is_deleted=False,
    )


def _formula(department_id, position, **overrides):
    values = dict(
        name="Lifecycle formula",
        department_id=department_id,
        position_id=position.id,
        position_name=position.name,
        user_level="L2",
        categories=[{
            "name": "Attendance", "weight": 1.0, "max_score": 5.0,
            "evaluation_type": "DEDUCTION",
            "deductions": [{
                "name": "Late Arrival", "measure_type": "count",
                "rules": [{"operator": ">=", "value": 2, "deduct_score": 2}],
            }],
        }],
        scope=FormulaScope.DEPARTMENT,
        is_active=True,
        status=FormulaStatus.ACTIVE,
        version=1,
        is_deleted=False,
        effective_month="2026-11",  # NEXT month: the legacy gate fails
        scoring_rules=[KPIScoringRuleItem(
            category_name="attendance", rule_name="late arrival",
            measure_type="count", operator=">=", target_value=2,
            deduct_score=2,
        )],
    )
    values.update(overrides)
    return KPIFormula(**values)


@pytest.fixture()
def world(tmp_path):
    db_file = tmp_path / "formula.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    BaseModel.metadata.create_all(sync_engine)
    session = sessionmaker(sync_engine)()
    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    sa_role = Role(name="Super Admin")
    staff_role = Role(name="Employee")
    sales = Department(name="Sales")
    session.add_all([sa_role, staff_role, sales])
    session.flush()
    position = Position(name="Officer", department_id=sales.id)
    sa = _user(sa_role, "sa@x.com", "Super")
    emp = _user(staff_role, "emp@x.com", "Worker")
    emp.position = position
    emp.user_departments = [
        UserDepartment(department_id=sales.id, user_level="L2", is_deleted=False)
    ]
    oct_period = _monthly_period("October 2026", 2026, 10)
    nov_period = _monthly_period("November 2026", 2026, 11)
    sep_period = _monthly_period("September 2026", 2026, 9)
    session.add_all([position, sa, emp, oct_period, nov_period, sep_period])
    # Pre-seed the shared category lookup: its id default emits the
    # Postgres-only gen_random_uuid() SQL function, so API-created
    # categories cannot insert on SQLite (explicit ids avoid it).
    from uuid import uuid4

    from app.modules.kpi.models import KpiCategory

    session.add_all([
        KpiCategory(id=uuid4(), name="Attendance"),
        KpiCategory(id=uuid4(), name="အချိန်မှီမတင်ပြခြင်း"),
    ])
    session.commit()

    actor = {"user": sa}

    async def override_get_db():
        async with factory() as db:
            yield db

    async def override_user():
        return actor["user"]

    main_module.app.dependency_overrides[get_db] = override_get_db
    main_module.app.dependency_overrides[get_current_user] = override_user
    limiter.enabled = False
    client = TestClient(main_module.app, raise_server_exceptions=False)
    yield type("W", (), {
        "session": session, "factory": factory, "client": client,
        "actor": actor, "sa": sa, "emp": emp, "sales": sales,
        "position": position, "oct": oct_period, "nov": nov_period,
        "sep": sep_period, "db_file": db_file,
    })()
    main_module.app.dependency_overrides.pop(get_db, None)
    main_module.app.dependency_overrides.pop(get_current_user, None)
    limiter.enabled = True
    session.close()


def _assign(world, formula, period):
    world.session.add(KPIAssignment(
        department_id=world.sales.id, position_id=world.position.id,
        user_id=world.emp.id, kpi_formula_id=formula.id,
        kpi_period_id=period.id, assigned_at=datetime.now(UTC),
    ))
    world.session.commit()


def _download(world, period):
    return world.client.get(
        "/api/v1/kpi-uploads/export-template",
        params={"period_id": str(period.id)},
    )


# ----------------------------------------------------------------------
# The read rule through the real download endpoint
# ----------------------------------------------------------------------

def test_zenn_case_link_overrides_the_month_gate(world):
    """Repro of Zenn's report: period exists, formula assigned —
    download failed because effective_month is next month. Linking
    the formula to the period (rule 2) is the fix."""
    formula = _formula(world.sales.id, world.position)
    world.session.add(formula)
    world.session.commit()
    _assign(world, formula, world.oct)

    response = _download(world, world.oct)
    assert response.status_code == 400
    assert "No KPI formula applies to this period yet" in str(response.json()["detail"])

    world.session.add(KPIFormulaPeriod(
        formula_id=formula.id, period_id=world.oct.id, is_current=True
    ))
    world.session.commit()
    assert _download(world, world.oct).status_code == 200


def test_rule4_history_only_link_applies_nowhere(world):
    formula = _formula(world.sales.id, world.position, effective_month="2020-01")
    world.session.add(formula)
    world.session.commit()
    _assign(world, formula, world.oct)
    world.session.add(KPIFormulaPeriod(
        formula_id=formula.id, period_id=world.oct.id, is_current=False
    ))
    world.session.commit()
    response = _download(world, world.oct)
    assert response.status_code == 400
    assert "No KPI formula applies to this period yet" in str(response.json()["detail"])


def test_rule1_closed_formula_applies_nowhere(world):
    formula = _formula(
        world.sales.id, world.position, effective_month="2020-01",
        closed_at=datetime.now(UTC),
    )
    world.session.add(formula)
    world.session.commit()
    _assign(world, formula, world.oct)
    world.session.add(KPIFormulaPeriod(
        formula_id=formula.id, period_id=world.oct.id, is_current=True
    ))
    world.session.commit()
    response = _download(world, world.oct)
    assert response.status_code == 400
    assert "No KPI formula applies to this period yet" in str(response.json()["detail"])


def test_error_split_no_employees_in_scope(world):
    formula = _formula(world.sales.id, world.position, effective_month="2020-01")
    world.session.add(formula)
    world.session.commit()
    world.session.add(KPIFormulaPeriod(
        formula_id=formula.id, period_id=world.oct.id, is_current=True
    ))
    # Nobody active at all: the employee is deactivated, and the
    # Super Admin row too (the request still runs on the already
    # authenticated in-memory actor — the scoped users query reads
    # the database, so the scope is genuinely empty).
    for uid in (world.emp.id, world.sa.id):
        world.session.get(User, uid).is_active = False
    world.session.commit()
    response = _download(world, world.oct)
    assert response.status_code == 400
    assert "No active employees are in scope" in str(response.json()["detail"])


def test_error_split_formula_matches_nobody(world):
    # Linked + live, but targets L1 while the only employee is L2.
    formula = _formula(
        world.sales.id, world.position,
        effective_month="2020-01", user_level="L1",
    )
    world.session.add(formula)
    world.session.commit()
    world.session.add(KPIFormulaPeriod(
        formula_id=formula.id, period_id=world.oct.id, is_current=True
    ))
    world.session.commit()
    response = _download(world, world.oct)
    assert response.status_code == 400
    assert "none of them match the employees in scope" in str(response.json()["detail"])


# ----------------------------------------------------------------------
# Link management + close + revive endpoints
# ----------------------------------------------------------------------

def test_period_link_endpoints_keep_history(world):
    formula = _formula(world.sales.id, world.position, effective_month="2020-01")
    world.session.add(formula)
    world.session.commit()
    fid = formula.id

    response = world.client.put(
        f"/api/v1/kpi/formulas/{fid}/periods",
        json={"period_ids": [str(world.oct.id)]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["period_ids"] == [str(world.oct.id)]
    assert response.json()["lifecycle_status"] == "linked"

    response = world.client.put(
        f"/api/v1/kpi/formulas/{fid}/periods",
        json={"period_ids": [str(world.nov.id)]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["period_ids"] == [str(world.nov.id)]
    links = world.session.execute(
        select(KPIFormulaPeriod).where(KPIFormulaPeriod.formula_id == fid)
    ).scalars().all()
    # The October row was KEPT as history, not deleted.
    assert len(links) == 2
    by_period = {link.period_id: link.is_current for link in links}
    assert by_period[world.oct.id] is False
    assert by_period[world.nov.id] is True

    response = world.client.put(
        f"/api/v1/kpi/formulas/{fid}/periods",
        json={"period_ids": ["00000000-0000-0000-0000-000000000000"]},
    )
    assert response.status_code == 422
    assert "Create the period first" in str(response.json()["detail"])


def test_close_and_revive_cycle(world):
    formula = _formula(world.sales.id, world.position, effective_month="2020-01")
    world.session.add(formula)
    world.session.commit()
    fid = formula.id
    world.session.add(KPIFormulaPeriod(
        formula_id=fid, period_id=world.oct.id, is_current=True
    ))
    world.session.commit()
    rules_before = world.session.execute(
        select(KPIScoringRuleItem).where(KPIScoringRuleItem.formula_id == fid)
    ).scalars().all()
    assert len(rules_before) == 1

    response = world.client.post(f"/api/v1/kpi/formulas/{fid}/close")
    assert response.status_code == 200, response.text
    assert response.json()["is_closed"] is True
    assert response.json()["lifecycle_status"] == "closed"
    assert response.json()["period_ids"] == []
    # Second close is a clear refusal.
    assert world.client.post(f"/api/v1/kpi/formulas/{fid}/close").status_code == 400

    # Revive without a period link is refused, with the reason.
    response = world.client.post(
        f"/api/v1/kpi/formulas/{fid}/revive", json={"period_ids": []}
    )
    assert response.status_code == 422
    assert "requires linking at least one period" in str(response.json()["detail"])

    response = world.client.post(
        f"/api/v1/kpi/formulas/{fid}/revive",
        json={"period_ids": [str(world.nov.id)]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_closed"] is False and body["closed_at"] is None
    assert body["period_ids"] == [str(world.nov.id)]
    # The rules were reused untouched — same record, same rules.
    world.session.expire_all()
    rules_after = world.session.execute(
        select(KPIScoringRuleItem).where(KPIScoringRuleItem.formula_id == fid)
    ).scalars().all()
    assert [r.id for r in rules_after] == [r.id for r in rules_before]
    actions = world.session.execute(
        select(AuditLog.action).where(
            AuditLog.action.in_(["FORMULA_CLOSED", "FORMULA_REVIVED"])
        )
    ).scalars().all()
    assert sorted(actions) == ["FORMULA_CLOSED", "FORMULA_REVIVED"]
    # Reviving an open formula is refused.
    response = world.client.post(
        f"/api/v1/kpi/formulas/{fid}/revive",
        json={"period_ids": [str(world.nov.id)]},
    )
    assert response.status_code == 400


def test_create_formula_with_period_ids(world):
    payload = {
        "name": "Created via API",
        "department_id": str(world.sales.id),
        "position_name": world.position.name,
        "position_id": str(world.position.id),
        "user_level": "L2",
        "categories": [
            {
                "name": "အချိန်မှီမတင်ပြခြင်း", "weight": 0.5, "max_score": 5.0,
                "evaluation_type": "DEDUCTION",
                "deductions": [{
                    "name": "Late", "measure_type": "count",
                    "rules": [{"operator": ">=", "value": 2, "deduct_score": 2}],
                }],
            },
            {
                "name": "Attendance", "weight": 0.5, "max_score": 5.0,
                "evaluation_type": "DEDUCTION",
                "deductions": [{
                    "name": "Late Arrival", "measure_type": "count",
                    "rules": [{"operator": ">=", "value": 2, "deduct_score": 2}],
                }],
            },
        ],
        "period_ids": [str(world.oct.id)],
    }
    response = world.client.post("/api/v1/kpi/formulas", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["period_ids"] == [str(world.oct.id)]
    assert response.json()["lifecycle_status"] == "linked"


# ----------------------------------------------------------------------
# Scheduler lifecycle: warn at 7 days, auto-close after the end
# ----------------------------------------------------------------------

def test_lifecycle_job_warns_closes_and_spares_legacy(world, monkeypatch):
    import app.background_jobs.scheduler as scheduler_module

    session = world.session
    ending = _formula(world.sales.id, world.position, name="Ending formula")
    ended = _formula(world.sales.id, world.position, name="Ended formula")
    future = _formula(world.sales.id, world.position, name="Future formula")
    legacy = _formula(
        world.sales.id, world.position, name="Legacy formula",
        effective_month="2020-01",
    )
    session.add_all([ending, ended, future, legacy])
    session.flush()
    session.add_all([
        KPIFormulaPeriod(formula_id=ending.id, period_id=world.oct.id, is_current=True),
        KPIFormulaPeriod(formula_id=ended.id, period_id=world.sep.id, is_current=True),
        KPIFormulaPeriod(formula_id=future.id, period_id=world.nov.id, is_current=True),
    ])
    session.commit()
    ending_id, ended_id, future_id, legacy_id = ending.id, ended.id, future.id, legacy.id

    monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", world.factory)
    # 2026-10-25 (Yangon): October period ends Nov 1 -> exactly 7 days.
    now = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
    result = asyncio.run(scheduler_module.run_formula_lifecycle_job(now=now))
    assert result == {"warned": 1, "closed": 1}

    session.expire_all()
    closed_formula = session.get(KPIFormula, ended_id)
    assert closed_formula.closed_at is not None
    ended_links = session.execute(
        select(KPIFormulaPeriod).where(KPIFormulaPeriod.formula_id == ended_id)
    ).scalars().all()
    assert ended_links and all(not link.is_current for link in ended_links)
    assert session.get(KPIFormula, ending_id).closed_at is None
    assert session.get(KPIFormula, future_id).closed_at is None
    assert session.get(KPIFormula, legacy_id).closed_at is None

    notes = session.execute(
        select(Notification).where(Notification.user_id == world.sa.id)
    ).scalars().all()
    titles = sorted(n.title for n in notes)
    assert titles == ["KPI formula closed", "KPI formula closing soon"]
    warning = next(n for n in notes if n.title == "KPI formula closing soon")
    # November exists in this world, so no "create a period" note.
    assert "No later period exists yet" not in warning.message
    audit = session.execute(
        select(AuditLog).where(AuditLog.action == "FORMULA_AUTO_CLOSED")
    ).scalars().all()
    assert len(audit) == 1

    # Idempotent at the same moment: the warning is deduped and
    # the closed formula stays closed — nothing new fires.
    result = asyncio.run(scheduler_module.run_formula_lifecycle_job(now=now))
    assert result == {"warned": 0, "closed": 0}

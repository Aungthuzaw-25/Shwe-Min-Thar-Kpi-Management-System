import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.dependencies import get_current_user
from app.database.connection import Base, get_db
from app.models.audit_log import AuditLog
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi import dashboard_router as dashboard_router_module
from app.modules.kpi.dashboard_router import unified_router
from app.modules.kpi.executive_intelligence_service import (
    _trend_change,
    build_executive_overview,
    build_monthly_average_series,
    get_executive_employee_detail,
    list_executive_employee_results,
)
from app.modules.kpi.models import (
    KPIFormula,
    KPIFormulaPeriod,
    KPIHistory,
    KPIPeriod,
    KPIPeriodStatus,
    KPIRecord,
    KPIStatus,
    PeriodType,
)
from app.modules.kpi.period_router import router as kpi_period_router
from app.modules.users.models import Permission, Position, Role, RolePermission, User

TABLES = [
    Department.__table__,
    Role.__table__,
    Permission.__table__,
    RolePermission.__table__,
    Position.__table__,
    User.__table__,
    UserDepartment.__table__,
    KPIFormula.__table__,
        KPIFormulaPeriod.__table__,
    KPIPeriod.__table__,
    KPIRecord.__table__,
    KPIHistory.__table__,
    AuditLog.__table__,
]


async def _with_isolated_database(database_path, test_case):
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
            await test_case(db)
    finally:
        await engine.dispose()


def _run_isolated_test(tmp_path, test_case):
    asyncio.run(
        _with_isolated_database(
            tmp_path / "executive-intelligence.sqlite", test_case
        )
    )


async def _create_fixture(
    db,
    *,
    with_history=False,
    role_name=None,
    with_kpi_view_permission=True,
):
    permissions = (
        [Permission(code="kpi.view")] if with_kpi_view_permission else []
    )
    role = Role(
        name=role_name or f"Executive test role {uuid4()}",
        permissions=permissions,
    )
    department_a = Department(name=f"Executive department A {uuid4()}")
    department_b = Department(name=f"Executive department B {uuid4()}")
    db.add_all([role, department_a, department_b])
    await db.flush()

    users = []
    for index, department in enumerate(
        [department_a, department_a, department_b, department_b], start=1
    ):
        user = User(
            email=f"{uuid4()}@example.test",
            hashed_password=uuid4().hex,
            first_name=f"Employee{index}",
            last_name="Test",
            role_id=role.id,
            role=role,
            permission_overrides=[],
            is_active=True,
            is_deleted=False,
        )
        db.add(user)
        await db.flush()
        db.add(
            UserDepartment(
                user_id=user.id,
                department_id=department.id,
                user_level="L2",
                is_deleted=False,
            )
        )
        users.append(user)

    deleted_user = User(
        email=f"{uuid4()}@example.test",
        hashed_password=uuid4().hex,
        first_name="Deleted",
        last_name="Employee",
        role_id=role.id,
        role=role,
        permission_overrides=[],
        is_active=True,
        is_deleted=True,
    )
    db.add(deleted_user)
    await db.flush()

    previous_period = KPIPeriod(
        label="Previous finalized period",
        start_date=datetime(2026, 5, 1, tzinfo=UTC),
        end_date=datetime(2026, 5, 31, 23, 59, tzinfo=UTC),
        status=KPIPeriodStatus.FINALIZED,
        is_deleted=False,
    )
    current_period = KPIPeriod(
        label="Current finalized period",
        start_date=datetime(2026, 6, 1, tzinfo=UTC),
        end_date=datetime(2026, 6, 30, 23, 59, tzinfo=UTC),
        status=KPIPeriodStatus.FINALIZED,
        is_deleted=False,
    )
    open_period = KPIPeriod(
        label="Open period",
        start_date=datetime(2026, 7, 1, tzinfo=UTC),
        end_date=datetime(2026, 7, 31, 23, 59, tzinfo=UTC),
        status=KPIPeriodStatus.OPEN,
        is_deleted=False,
    )
    db.add_all([previous_period, current_period, open_period])
    await db.flush()

    def record(user, department, period, percentage, status):
        return KPIRecord(
            user_id=user.id,
            department_id=department.id,
            kpi_period_id=period.id,
            period_type=PeriodType.MONTHLY,
            period_start=period.start_date,
            period_end=period.end_date,
            raw_score=percentage / 20,
            weighted_score=percentage / 20,
            percentage=percentage,
            status_bucket=status,
            is_deleted=False,
        )

    current_scores = [(90, KPIStatus.EXCELLENT), (30, KPIStatus.POOR), (80, KPIStatus.VERY_GOOD)]
    previous_scores = [(60, KPIStatus.GOOD), (60, KPIStatus.GOOD), (60, KPIStatus.GOOD)]
    records = [
        record(users[index], department_a if index < 2 else department_b, current_period, score, status)
        for index, (score, status) in enumerate(current_scores)
    ]
    records.extend(
        record(users[index], department_a if index < 2 else department_b, previous_period, score, status)
        for index, (score, status) in enumerate(previous_scores)
    )
    records.append(record(deleted_user, department_b, current_period, 100, KPIStatus.EXCELLENT))
    deleted_record = record(
        users[3], department_b, current_period, 100, KPIStatus.EXCELLENT
    )
    deleted_record.is_deleted = True
    records.append(deleted_record)
    db.add_all(records)
    await db.flush()
    if with_history:
        db.add(
            KPIHistory(
                kpi_record_id=records[0].id,
                old_percentage=80,
                new_percentage=90,
                old_status=KPIStatus.VERY_GOOD,
                new_status=KPIStatus.EXCELLENT,
                reason="Test fixture transition",
                is_deleted=False,
            )
        )
        await db.flush()
    return (
        users,
        department_a,
        department_b,
        previous_period,
        current_period,
        open_period,
    )


async def _test_summary_departments_trend_and_deleted_users(db):
    users, department_a, department_b, _, current_period, _ = await _create_fixture(db)
    overview = await build_executive_overview(
        db,
        scope_user_ids=None,
        department_ids=[department_a.id, department_b.id],
        show_departments=True,
        period_id=current_period.id,
    )

    assert overview.summary.average_percentage == 66.67
    assert overview.summary.evaluated_employee_count == 3
    assert overview.summary.active_employee_count == 4
    assert overview.summary.no_result_employee_count == 1
    assert [(item.status, item.count, item.percentage) for item in overview.summary.status_distribution] == [
        (KPIStatus.EXCELLENT, 1, 33.33),
        (KPIStatus.VERY_GOOD, 1, 33.33),
        (KPIStatus.GOOD, 0, 0),
        (KPIStatus.FAIR, 0, 0),
        (KPIStatus.POOR, 1, 33.33),
    ]
    departments = {item.department_id: item for item in overview.departments}
    assert departments[department_a.id].evaluated_employee_count == 2
    assert departments[department_a.id].below_fair_percentage == 50
    assert departments[department_b.id].no_result_employee_count == 1
    assert overview.trend.direction == "UP"
    assert overview.trend.delta == 6.67
    assert overview.trend.points[-1].period_id == current_period.id
    assert any(signal.signal_type == "COVERAGE" for signal in overview.signals)
    assert overview.category_intelligence.status == "unavailable"
    assert overview.target_intelligence.status == "unavailable"


def test_summary_department_aggregation_and_period_trend(tmp_path):
    _run_isolated_test(tmp_path, _test_summary_departments_trend_and_deleted_users)


async def _test_month_range_summary_and_employee_ranking(db):
    users, department_a, department_b, previous_period, current_period, _ = (
        await _create_fixture(db)
    )
    overview = await build_executive_overview(
        db,
        scope_user_ids=None,
        department_ids=[department_a.id, department_b.id],
        show_departments=True,
        period_start=datetime(2026, 5, 1, tzinfo=UTC),
        period_end=datetime(2026, 7, 1, tzinfo=UTC),
    )

    assert overview.selected_period.id == current_period.id
    assert overview.summary.average_percentage == 63.33
    assert overview.summary.evaluated_employee_count == 3
    assert overview.summary.no_result_employee_count == 1
    assert [point.period_id for point in overview.trend.points] == [
        previous_period.id,
        current_period.id,
    ]
    assert overview.trend.delta == 6.67
    assert len(overview.departments) == 2

    employees = await list_executive_employee_results(
        db,
        period_id=None,
        period_start=datetime(2026, 5, 1, tzinfo=UTC),
        period_end=datetime(2026, 7, 1, tzinfo=UTC),
        scope_user_ids=None,
        department_ids=[department_a.id, department_b.id],
        offset=0,
        limit=25,
    )
    assert employees.total == 3
    assert [item.user_id for item in employees.items] == [
        users[0].id,
        users[2].id,
        users[1].id,
    ]
    assert [item.percentage for item in employees.items] == [75.0, 70.0, 45.0]


def test_month_range_scopes_summary_trend_and_employee_ranking(tmp_path):
    _run_isolated_test(tmp_path, _test_month_range_summary_and_employee_ranking)


async def _test_monthly_average_series_has_calendar_gaps_and_respects_scope(db):
    users, department_a, department_b, _, _, _ = await _create_fixture(db)
    monthly = await build_monthly_average_series(
        db,
        year=2026,
        scope_user_ids=None,
        department_ids=[department_a.id, department_b.id],
    )
    assert len(monthly) == 12
    assert [point.month for point in monthly] == list(range(1, 13))
    assert [point.average_percentage for point in monthly] == [
        None, None, None, None, 60.0, 66.67, None, None, None, None, None, None
    ]
    assert [point.evaluated_employee_count for point in monthly] == [
        0, 0, 0, 0, 3, 3, 0, 0, 0, 0, 0, 0
    ]

    department_monthly = await build_monthly_average_series(
        db,
        year=2026,
        scope_user_ids=[users[0].id, users[1].id],
        department_ids=[department_a.id],
    )
    assert department_monthly[5].average_percentage == 60.0
    assert department_monthly[5].evaluated_employee_count == 2

    no_data_year = await build_monthly_average_series(
        db,
        year=2027,
        scope_user_ids=None,
        department_ids=[department_a.id, department_b.id],
    )
    assert len(no_data_year) == 12
    assert all(point.average_percentage is None for point in no_data_year)


def test_monthly_average_series_preserves_empty_months_and_scope(tmp_path):
    _run_isolated_test(
        tmp_path, _test_monthly_average_series_has_calendar_gaps_and_respects_scope
    )


async def _test_open_empty_invalid_and_scoped_detail(db):
    users, department_a, _, _, current_period, open_period = await _create_fixture(
        db, with_history=True
    )
    with pytest.raises(LookupError):
        await build_executive_overview(
            db,
            scope_user_ids=[users[0].id],
            department_ids=[department_a.id],
            show_departments=True,
            period_id=open_period.id,
        )

    overview = await build_executive_overview(
        db,
        scope_user_ids=[users[0].id],
        department_ids=[department_a.id],
        show_departments=True,
    )
    period_options = {item.id: item for item in overview.periods}
    assert current_period.id in period_options
    assert open_period.id not in period_options
    assert set(period_options[current_period.id].model_dump()) == {
        "id",
        "label",
        "period_type",
        "start_date",
        "end_date",
        "status",
    }
    assert period_options[current_period.id].period_type == PeriodType.MONTHLY

    scoped_detail = await get_executive_employee_detail(
        db,
        user_id=users[0].id,
        period_id=current_period.id,
        scope_user_ids=[users[0].id],
        department_ids=[department_a.id],
    )
    assert scoped_detail is not None
    assert scoped_detail.percentage == 90
    assert scoped_detail.history[0].percentage == 90
    assert scoped_detail.category_intelligence.status == "unavailable"

    employee_page = await list_executive_employee_results(
        db,
        period_id=current_period.id,
        scope_user_ids=[users[0].id],
        department_ids=[department_a.id],
        offset=0,
        limit=25,
    )
    assert employee_page.total == 1
    assert [item.user_id for item in employee_page.items] == [users[0].id]

    unauthorized_detail = await get_executive_employee_detail(
        db,
        user_id=users[2].id,
        period_id=current_period.id,
        scope_user_ids=[users[0].id],
        department_ids=[department_a.id],
    )
    assert unauthorized_detail is None

    with pytest.raises(LookupError):
        await build_executive_overview(
            db,
            scope_user_ids=[users[0].id],
            department_ids=[department_a.id],
            show_departments=False,
            period_id=uuid4(),
        )


def test_period_empty_state_and_employee_scope(tmp_path):
    _run_isolated_test(tmp_path, _test_open_empty_invalid_and_scoped_detail)


async def _test_api_enforces_l2_scope_for_employee_drilldown(db):
    users, department_a, department_b, _, current_period, _ = await _create_fixture(db)
    app = FastAPI()
    app.include_router(unified_router, prefix="/api/v1/dashboard")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: users[0]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        overview_response = await client.get(
            "/api/v1/dashboard/executive/overview",
            params={"period_id": str(current_period.id)},
        )
        assert overview_response.status_code == 200
        overview = overview_response.json()
        assert overview["summary"]["evaluated_employee_count"] == 1
        assert overview["departments"] == []

        monthly_response = await client.get(
            "/api/v1/dashboard/executive/monthly-averages",
            params={"year": 2026},
        )
        assert monthly_response.status_code == 200
        monthly = monthly_response.json()
        assert len(monthly["monthly_averages"]) == 12
        assert monthly["monthly_averages"][5]["average_percentage"] == 90
        assert monthly["monthly_averages"][5]["evaluated_employee_count"] == 1
        assert monthly["monthly_averages"][4]["average_percentage"] == 60
        assert [item["id"] for item in monthly["departments"]] == [
            str(department_a.id)
        ]

        selected_department_response = await client.get(
            "/api/v1/dashboard/executive/monthly-averages",
            params={"year": 2026, "department_id": str(department_a.id)},
        )
        assert selected_department_response.status_code == 200
        selected_department = selected_department_response.json()
        assert selected_department["monthly_averages"][5]["average_percentage"] == 90
        assert selected_department["monthly_averages"][5]["evaluated_employee_count"] == 1

        unauthorized_department_response = await client.get(
            "/api/v1/dashboard/executive/monthly-averages",
            params={"year": 2026, "department_id": str(department_b.id)},
        )
        assert unauthorized_department_response.status_code == 404

        invalid_year_response = await client.get(
            "/api/v1/dashboard/executive/monthly-averages",
            params={"year": 1899},
        )
        assert invalid_year_response.status_code == 422

        allowed_response = await client.get(
            f"/api/v1/dashboard/executive/employees/{users[0].id}",
            params={"period_id": str(current_period.id)},
        )
        assert allowed_response.status_code == 200
        assert allowed_response.json()["user_id"] == str(users[0].id)

        unauthorized_response = await client.get(
            f"/api/v1/dashboard/executive/employees/{users[2].id}",
            params={"period_id": str(current_period.id)},
        )
        assert unauthorized_response.status_code == 404

        membership = await db.scalar(
            select(UserDepartment).where(UserDepartment.user_id == users[0].id)
        )
        membership.user_level = "L1"
        await db.flush()
        manager_response = await client.get(
            "/api/v1/dashboard/executive/overview",
            params={"period_id": str(current_period.id)},
        )
        manager_overview = manager_response.json()
        assert manager_response.status_code == 200
        assert manager_overview["summary"]["evaluated_employee_count"] == 2
        assert [
            item["department_id"] for item in manager_overview["departments"]
        ] == [str(department_a.id)]
        cross_department_response = await client.get(
            f"/api/v1/dashboard/executive/employees/{users[2].id}",
            params={"period_id": str(current_period.id)},
        )
        assert cross_department_response.status_code == 404

        invalid_period_response = await client.get(
            "/api/v1/dashboard/executive/overview",
            params={"period_id": str(uuid4())},
        )
        assert invalid_period_response.status_code == 404


def test_executive_api_enforces_l2_scope_and_invalid_period(tmp_path):
    _run_isolated_test(tmp_path, _test_api_enforces_l2_scope_for_employee_drilldown)


async def _test_super_admin_department_filter_scopes_overview_and_employees(db):
    users, department_a, department_b, _, current_period, _ = await _create_fixture(
        db, role_name="Super Admin"
    )
    app = FastAPI()
    app.include_router(unified_router, prefix="/api/v1/dashboard")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: users[0]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        params = {
            "period_id": str(current_period.id),
            "department_id": str(department_a.id),
        }
        overview_response = await client.get(
            "/api/v1/dashboard/executive/overview", params=params
        )
        assert overview_response.status_code == 200
        overview = overview_response.json()
        assert overview["summary"]["average_percentage"] == 60
        assert overview["summary"]["evaluated_employee_count"] == 2
        assert overview["summary"]["active_employee_count"] == 2
        assert [item["department_id"] for item in overview["departments"]] == [
            str(department_a.id)
        ]

        employee_response = await client.get(
            "/api/v1/dashboard/executive/employees",
            params={
                "period_start": "2026-06-01T00:00:00+00:00",
                "period_end": "2026-07-01T00:00:00+00:00",
                "department_id": str(department_a.id),
            },
        )
        assert employee_response.status_code == 200
        assert employee_response.json()["total"] == 2
        assert {
            item["department_name"] for item in employee_response.json()["items"]
        } == {department_a.name}

        invalid_department_response = await client.get(
            "/api/v1/dashboard/executive/overview",
            params={
                "period_id": str(current_period.id),
                "department_id": str(uuid4()),
            },
        )
        assert invalid_department_response.status_code == 404

        cross_department_detail = await client.get(
            f"/api/v1/dashboard/executive/employees/{users[2].id}",
            params={
                "period_id": str(current_period.id),
                "department_id": str(department_a.id),
            },
        )
        assert cross_department_detail.status_code == 404


def test_super_admin_department_filter_scopes_all_executive_kpi_data(tmp_path):
    _run_isolated_test(
        tmp_path, _test_super_admin_department_filter_scopes_overview_and_employees
    )


async def _test_monthly_averages_denies_missing_kpi_permission(db):
    users, *_ = await _create_fixture(db, with_kpi_view_permission=False)
    app = FastAPI()
    app.include_router(unified_router, prefix="/api/v1/dashboard")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: users[0]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/dashboard/executive/monthly-averages",
            params={"year": 2026},
        )

    assert response.status_code == 403


def test_monthly_averages_denies_users_without_kpi_view_permission(tmp_path):
    _run_isolated_test(tmp_path, _test_monthly_averages_denies_missing_kpi_permission)


async def _test_super_admin_monthly_averages_returns_expected_payload(db):
    users, department_a, department_b, *_ = await _create_fixture(
        db, role_name="Super Admin", with_kpi_view_permission=False
    )
    app = FastAPI()
    app.include_router(unified_router, prefix="/api/v1/dashboard")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: users[0]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/dashboard/executive/monthly-averages",
            params={"year": 2026},
        )

    assert response.status_code == 200
    payload = response.json()
    assert {item["id"] for item in payload["departments"]} == {
        str(department_a.id),
        str(department_b.id),
    }
    assert len(payload["monthly_averages"]) == 12
    assert payload["monthly_averages"][5] == {
        "month": 6,
        "average_percentage": 66.67,
        "evaluated_employee_count": 3,
    }


def test_super_admin_monthly_averages_returns_expected_payload(tmp_path):
    _run_isolated_test(
        tmp_path, _test_super_admin_monthly_averages_returns_expected_payload
    )


async def _test_monthly_averages_rejects_missing_and_invalid_auth():
    app = FastAPI()
    app.include_router(unified_router, prefix="/api/v1/dashboard")

    async def override_db():
        yield None

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.get(
            "/api/v1/dashboard/executive/monthly-averages",
            params={"year": 2026},
        )
        invalid = await client.get(
            "/api/v1/dashboard/executive/monthly-averages",
            params={"year": 2026},
            headers={"Authorization": "Bearer invalid.token.value"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_monthly_averages_requires_valid_authentication():
    asyncio.run(_test_monthly_averages_rejects_missing_and_invalid_auth())


async def _test_monthly_averages_database_error_is_not_exposed(db, monkeypatch):
    users, *_ = await _create_fixture(db, role_name="Super Admin")
    app = FastAPI(debug=False)
    app.include_router(unified_router, prefix="/api/v1/dashboard")

    async def override_db():
        yield db

    async def fail_monthly_query(*_args, **_kwargs):
        raise RuntimeError("private-database-secret")

    monkeypatch.setattr(
        dashboard_router_module,
        "build_monthly_average_series",
        fail_monthly_query,
    )
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: users[0]
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/v1/dashboard/executive/monthly-averages",
            params={"year": 2026},
        )

    assert response.status_code == 500
    assert "private-database-secret" not in response.text
    assert "Traceback" not in response.text


def test_monthly_averages_does_not_expose_database_errors(tmp_path, monkeypatch):
    _run_isolated_test(
        tmp_path,
        lambda db: _test_monthly_averages_database_error_is_not_exposed(
            db, monkeypatch
        ),
    )


async def _test_finalized_period_without_records_is_not_a_fake_zero(db):
    users, department_a, _, _, _, _ = await _create_fixture(db)
    empty_period = KPIPeriod(
        label="Finalized period with no results",
        start_date=datetime(2026, 8, 1, tzinfo=UTC),
        end_date=datetime(2026, 8, 31, 23, 59, tzinfo=UTC),
        status=KPIPeriodStatus.FINALIZED,
        is_deleted=False,
    )
    db.add(empty_period)
    await db.flush()

    overview = await build_executive_overview(
        db,
        scope_user_ids=[user.id for user in users],
        department_ids=[department_a.id],
        show_departments=True,
    )
    assert empty_period.id not in {item.id for item in overview.periods}
    with pytest.raises(LookupError, match="KPI period not found\\."):
        await build_executive_overview(
            db,
            scope_user_ids=[user.id for user in users],
            department_ids=[department_a.id],
            show_departments=True,
            period_id=empty_period.id,
        )


def test_finalized_period_without_results_returns_empty_state(tmp_path):
    _run_isolated_test(
        tmp_path, _test_finalized_period_without_records_is_not_a_fake_zero
    )


async def _test_super_admin_can_select_locked_period_without_results(db):
    await _create_fixture(db)
    super_admin_role = Role(name="Super Admin")
    db.add(super_admin_role)
    await db.flush()
    super_admin = User(
        email=f"{uuid4()}@example.test",
        hashed_password=uuid4().hex,
        first_name="Super",
        last_name="Admin",
        role_id=super_admin_role.id,
        role=super_admin_role,
        is_active=True,
        is_deleted=False,
    )
    locked_period = KPIPeriod(
        label="Locked period without results",
        start_date=datetime(2026, 10, 1, tzinfo=UTC),
        end_date=datetime(2026, 11, 1, tzinfo=UTC),
        status=KPIPeriodStatus.LOCKED,
        is_deleted=False,
    )
    open_period = KPIPeriod(
        label="Open period without results",
        start_date=datetime(2026, 11, 1, tzinfo=UTC),
        end_date=datetime(2026, 12, 1, tzinfo=UTC),
        status=KPIPeriodStatus.OPEN,
        is_deleted=False,
    )
    db.add_all([super_admin, locked_period, open_period])
    await db.flush()

    app = FastAPI()
    app.include_router(unified_router, prefix="/api/v1/dashboard")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: super_admin
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/dashboard/executive/overview",
            params={"period_id": str(locked_period.id)},
        )
        default_response = await client.get(
            "/api/v1/dashboard/executive/overview"
        )

    assert response.status_code == 200
    assert default_response.status_code == 200
    overview = response.json()
    locked_options = {
        item["id"]: item for item in overview["periods"]
    }
    assert str(locked_period.id) in locked_options
    assert locked_options[str(locked_period.id)]["status"] == "LOCKED"
    assert locked_options[str(locked_period.id)]["period_type"] is None
    assert str(open_period.id) not in locked_options
    assert overview["selected_period"]["id"] == str(locked_period.id)
    assert overview["summary"]["average_percentage"] is None
    assert overview["summary"]["evaluated_employee_count"] == 0
    assert default_response.json()["selected_period"]["id"] == str(locked_period.id)


def test_super_admin_dashboard_lists_locked_period_without_results(tmp_path):
    _run_isolated_test(
        tmp_path, _test_super_admin_can_select_locked_period_without_results
    )


async def _test_reopening_locked_period_persists_without_dashboard_mutation(db):
    super_admin_role = Role(name="Super Admin")
    db.add(super_admin_role)
    await db.flush()
    super_admin = User(
        email=f"{uuid4()}@example.test",
        hashed_password=uuid4().hex,
        first_name="Super",
        last_name="Admin",
        role_id=super_admin_role.id,
        role=super_admin_role,
        is_active=True,
        is_deleted=False,
    )
    period = KPIPeriod(
        label="Reopen lifecycle period",
        start_date=datetime(2026, 9, 1, tzinfo=UTC),
        end_date=datetime(2026, 10, 1, tzinfo=UTC),
        status=KPIPeriodStatus.LOCKED,
        is_deleted=False,
    )
    db.add_all([super_admin, period])
    await db.flush()

    app = FastAPI()
    app.include_router(kpi_period_router, prefix="/api/v1/kpi")
    app.include_router(unified_router, prefix="/api/v1/dashboard")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: super_admin
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        reopen_response = await client.put(
            f"/api/v1/kpi/periods/{period.id}/status",
            params={"new_status": "OPEN", "reason": "Approved QA reopen"},
        )
        management_response = await client.get("/api/v1/kpi/periods/")
        dashboard_response = await client.get(
            "/api/v1/dashboard/executive/overview"
        )

    assert reopen_response.status_code == 200
    assert reopen_response.json()["status"] == "OPEN"
    assert management_response.status_code == 200
    listed_period = next(
        item
        for item in management_response.json()
        if item["id"] == str(period.id)
    )
    assert listed_period["status"] == "OPEN"
    assert dashboard_response.status_code == 200
    assert str(period.id) not in {
        item["id"] for item in dashboard_response.json()["periods"]
    }

    fresh_session_factory = async_sessionmaker(db.bind, expire_on_commit=False)
    async with fresh_session_factory() as fresh_db:
        persisted_period = await fresh_db.get(KPIPeriod, period.id)
        assert persisted_period.status == KPIPeriodStatus.OPEN
        await build_executive_overview(
            fresh_db,
            scope_user_ids=None,
            department_ids=None,
            show_departments=True,
        )
        await fresh_db.refresh(persisted_period)
        assert persisted_period.status == KPIPeriodStatus.OPEN


def test_reopening_locked_period_persists_across_dashboard_load(tmp_path):
    _run_isolated_test(
        tmp_path, _test_reopening_locked_period_persists_without_dashboard_mutation
    )


async def _test_period_options_follow_result_scope(db, level):
    users, department_a, department_b, _, _, _ = await _create_fixture(db)

    def period(label, month):
        return KPIPeriod(
            label=label,
            start_date=datetime(2026, month, 1, tzinfo=UTC),
            end_date=datetime(2026, month, 28, 23, 59, tzinfo=UTC),
            status=KPIPeriodStatus.FINALIZED,
            is_deleted=False,
        )

    own_period = period("Authorized own period", 8)
    colleague_period = period("Same-department colleague period", 9)
    cross_department_period = period("Cross-department-only period", 10)
    empty_period = period("No scoped result period", 11)
    db.add_all(
        [own_period, colleague_period, cross_department_period, empty_period]
    )
    await db.flush()

    def record(user, department, result_period, period_type):
        return KPIRecord(
            user_id=user.id,
            department_id=department.id,
            kpi_period_id=result_period.id,
            period_type=period_type,
            period_start=result_period.start_date,
            period_end=result_period.end_date,
            raw_score=4,
            weighted_score=4,
            percentage=80,
            status_bucket=KPIStatus.VERY_GOOD,
            is_deleted=False,
        )

    db.add_all(
        [
            record(users[0], department_a, own_period, PeriodType.MONTHLY),
            record(users[2], department_b, own_period, PeriodType.QUARTERLY),
            record(users[1], department_a, colleague_period, PeriodType.MONTHLY),
            record(
                users[2],
                department_b,
                cross_department_period,
                PeriodType.QUARTERLY,
            ),
        ]
    )
    await db.flush()

    if level in {"L0", "L1"}:
        scope_user_ids = [users[0].id, users[1].id]
    else:
        scope_user_ids = [users[0].id]

    overview = await build_executive_overview(
        db,
        scope_user_ids=scope_user_ids,
        department_ids=[department_a.id],
        show_departments=level != "L2",
    )
    visible = {item.id: item for item in overview.periods}
    assert own_period.id in visible
    assert visible[own_period.id].period_type == PeriodType.MONTHLY
    assert empty_period.id not in visible
    assert cross_department_period.id not in visible
    if level in {"L0", "L1"}:
        assert colleague_period.id in visible
        colleague_detail = await get_executive_employee_detail(
            db,
            user_id=users[1].id,
            period_id=colleague_period.id,
            scope_user_ids=scope_user_ids,
            department_ids=[department_a.id],
        )
        assert colleague_detail is not None
    else:
        assert colleague_period.id not in visible
        colleague_detail = await get_executive_employee_detail(
            db,
            user_id=users[1].id,
            period_id=colleague_period.id,
            scope_user_ids=scope_user_ids,
            department_ids=[department_a.id],
        )
        assert colleague_detail is None

    cross_department_detail = await get_executive_employee_detail(
        db,
        user_id=users[2].id,
        period_id=cross_department_period.id,
        scope_user_ids=scope_user_ids,
        department_ids=[department_a.id],
    )
    assert cross_department_detail is None

    assert set(visible[own_period.id].model_dump()) == {
        "id",
        "label",
        "period_type",
        "start_date",
        "end_date",
        "status",
    }
    assert not ({"user_id", "employee_name", "percentage", "raw_score"} & set(
        visible[own_period.id].model_dump()
    ))
    for inaccessible_period in (cross_department_period, empty_period):
        with pytest.raises(LookupError, match="KPI period not found\\."):
            await build_executive_overview(
                db,
                scope_user_ids=scope_user_ids,
                department_ids=[department_a.id],
                show_departments=level != "L2",
                period_id=inaccessible_period.id,
            )


@pytest.mark.parametrize("level", ["L0", "L1", "L2"])
def test_period_options_follow_effective_result_scope(tmp_path, level):
    _run_isolated_test(
        tmp_path,
        lambda db: _test_period_options_follow_result_scope(db, level),
    )


async def _test_no_period_and_no_previous_period_states(db):
    no_period = await build_executive_overview(
        db,
        scope_user_ids=None,
        department_ids=None,
        show_departments=True,
    )
    assert no_period.periods == []
    assert no_period.selected_period is None
    assert no_period.summary.average_percentage is None

    users, department_a, _, previous_period, current_period, _ = await _create_fixture(db)
    previous_period.is_deleted = True
    await db.flush()
    overview = await build_executive_overview(
        db,
        scope_user_ids=[users[0].id],
        department_ids=[department_a.id],
        show_departments=False,
        period_id=current_period.id,
    )
    assert len(overview.trend.points) == 1
    assert overview.trend.comparison_available is False
    assert overview.trend.delta is None


def test_no_period_and_no_previous_period_are_explicit(tmp_path):
    _run_isolated_test(tmp_path, _test_no_period_and_no_previous_period_states)


@pytest.mark.parametrize(
    ("current", "previous", "expected_delta", "expected_direction"),
    [
        (90, 80, 10, "UP"),
        (70, 80, -10, "DOWN"),
        (80, 80, 0, "FLAT"),
        (80.004, 80, 0, "UP"),
    ],
)
def test_trend_direction_uses_unrounded_delta(
    current, previous, expected_delta, expected_direction
):
    assert _trend_change(current, previous) == (expected_delta, expected_direction)

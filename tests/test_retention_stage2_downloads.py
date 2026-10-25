"""Retention Stage 2 acceptance — flexible downloads and grants.

All four window kinds x both scopes must return exactly the records
whose period start falls inside the window (departed employees
included, labelled), company files must equal the sum of the
department files, and the grant rules must hold at the API level:
no grant -> 403 anywhere; a department grant opens only that
department; a company grant opens everything; revoke closes again.
"""

import csv
import io
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.core.dependencies import get_current_user
from app.database.connection import Base, get_db
from app.models.audit_log import AuditLog
from app.models.report_download import ReportDownload
from app.modules.departments.models import Department
from app.modules.kpi.models import KPIRecord, KPIStatus, PeriodType
from app.modules.users.models import Role, User


def _user(role, email, first, last):
    return User(
        id=uuid.uuid4(), email=email, hashed_password="x",
        first_name=first, last_name=last, role=role, is_active=True,
    )


def _record(user, dept, start, score, *, resigned=None):
    return KPIRecord(
        user_id=user.id, department_id=dept.id,
        period_type=PeriodType.MONTHLY,
        period_start=datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
        period_end=datetime(start.year, start.month, 28, tzinfo=timezone.utc),
        weighted_score=score, raw_score=score, percentage=score * 20,
        status_bucket=KPIStatus.GOOD,
        employee_name=f"{user.first_name} {user.last_name}",
        department_name=dept.name,
        employee_resigned_at=resigned,
    )


@pytest.fixture()
def world(tmp_path):
    db_file = tmp_path / "downloads.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    session = sessionmaker(sync_engine)()
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    sa_role, admin_role, staff_role = Role(name="Super Admin"), Role(name="Admin"), Role(name="Employee")
    sales, hr_dept = Department(name="Sales"), Department(name="People")
    session.add_all([sa_role, admin_role, staff_role, sales, hr_dept])
    session.flush()
    sa = _user(sa_role, "sa@x.com", "Super", "Admin")
    admin = _user(admin_role, "admin@x.com", "Ann", "Admin")
    hr = _user(staff_role, "hr@x.com", "Hla", "Resource")
    e = _user(staff_role, "e@x.com", "Ko", "Departed")
    f = _user(staff_role, "f@x.com", "Ma", "Stayed")
    session.add_all([sa, admin, hr, e, f])
    session.flush()
    resigned = date(2025, 6, 15)
    session.add_all([
        _record(e, sales, date(2025, 3, 1), 4.0, resigned=resigned),
        _record(e, sales, date(2025, 12, 20), 2.0, resigned=resigned),
        _record(f, sales, date(2025, 3, 1), 3.0),
        _record(f, hr_dept, date(2025, 4, 1), 5.0),
        _record(f, hr_dept, date(2026, 1, 10), 1.0),
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
    client = TestClient(main_module.app, raise_server_exceptions=False)
    yield SimpleNamespace(
        session=session, client=client, actor=actor,
        sa=sa, admin=admin, hr=hr, e=e, f=f, sales=sales, hr_dept=hr_dept,
    )
    main_module.app.dependency_overrides.pop(get_db, None)
    main_module.app.dependency_overrides.pop(get_current_user, None)
    session.close()


def _data_rows(response):
    rows = list(csv.reader(io.StringIO(response.text)))
    header, body = rows[0], rows[1:]
    data = [r for r in body if r and not r[0].startswith(("Department average", "Company average"))]
    totals = [r for r in body if r and r[0].startswith(("Department average", "Company average"))]
    return header, data, totals


def _download(world, **params):
    return world.client.get("/api/v1/system/report-download", params=params)


def test_windows_and_scopes_return_exactly_the_in_window_records(world):
    # MONTHLY company: the two March 2025 records, departed labelled.
    r = _download(world, report_type="MONTHLY", year=2025, month=3, scope="company")
    assert r.status_code == 200, r.text
    assert 'filename="kpi-report_company_2025-03.csv"' in r.headers["content-disposition"]
    _, data, totals = _data_rows(r)
    assert len(data) == 2
    departed_rows = [row for row in data if row[0] == "Ko Departed"]
    assert departed_rows and departed_rows[0][8] == "Resigned · 2025-06-15"
    company_total = [t for t in totals if t[0] == "Company average"][0]
    assert company_total[5] == "3.50"  # (4+3)/2
    assert company_total[7] == "2 records"

    # QUARTERLY department: only Sales Q1 rows (Mar records), filename form.
    r = _download(world, report_type="QUARTERLY", year=2025, quarter=1,
                  scope="department", department_id=str(world.sales.id))
    assert r.status_code == 200, r.text
    assert 'filename="kpi-report_dept-Sales_2025-Q1.csv"' in r.headers["content-disposition"]
    _, data, _ = _data_rows(r)
    assert len(data) == 2 and all(row[2] == "Sales" for row in data)

    # CUSTOM spanning the year boundary: Dec 20 2025 + Jan 10 2026 in ONE file.
    r = _download(world, report_type="CUSTOM", start="2025-12-15", end="2026-01-15", scope="company")
    assert r.status_code == 200, r.text
    _, data, _ = _data_rows(r)
    assert len(data) == 2
    assert {row[3] for row in data} == {"2025-12-20", "2026-01-10"}

    # Company YEARLY == the two department files combined (row counts).
    _, company_data, _ = _data_rows(_download(world, report_type="YEARLY", year=2025, scope="company"))
    _, sales_data, _ = _data_rows(_download(world, report_type="YEARLY", year=2025, scope="department", department_id=str(world.sales.id)))
    _, people_data, _ = _data_rows(_download(world, report_type="YEARLY", year=2025, scope="department", department_id=str(world.hr_dept.id)))
    assert len(company_data) == 4
    assert len(sales_data) + len(people_data) == len(company_data)

    # Every download was recorded with its window + scope.
    rows = world.session.execute(select(ReportDownload)).scalars().all()
    assert len(rows) == 6
    yearly_company = [r for r in rows if r.report_type == "YEARLY" and r.is_company_wide]
    assert yearly_company and yearly_company[0].period_start == date(2025, 1, 1)
    audits = world.session.execute(
        select(AuditLog).where(AuditLog.action == "REPORT_DOWNLOAD")
    ).scalars().all()
    assert len(audits) == 6


def test_grant_rules_at_the_api_level(world):
    world.actor["user"] = world.hr
    # No grant: denied for both scopes (and a regular Admin too).
    assert _download(world, report_type="MONTHLY", year=2025, month=3, scope="company").status_code == 403
    assert _download(world, report_type="MONTHLY", year=2025, month=3, scope="department", department_id=str(world.sales.id)).status_code == 403
    world.actor["user"] = world.admin
    assert _download(world, report_type="MONTHLY", year=2025, month=3, scope="company").status_code == 403
    # Grant management itself is Super-Admin-only.
    assert world.client.get("/api/v1/system/report-downloads/grants").status_code == 403
    assert world.client.get("/api/v1/system/retention/lock-status", params={"year": 2025}).status_code == 403

    world.actor["user"] = world.sa
    created = world.client.post(
        "/api/v1/system/report-downloads/grants",
        json={"user_id": str(world.hr.id), "scope_type": "DEPARTMENT", "department_id": str(world.sales.id)},
    )
    assert created.status_code == 201, created.text
    grant_id = created.json()["id"]
    duplicate = world.client.post(
        "/api/v1/system/report-downloads/grants",
        json={"user_id": str(world.hr.id), "scope_type": "DEPARTMENT", "department_id": str(world.sales.id)},
    )
    assert duplicate.status_code == 409

    world.actor["user"] = world.hr
    # The granted department opens (any window), everything else stays shut.
    assert _download(world, report_type="YEARLY", year=2025, scope="department", department_id=str(world.sales.id)).status_code == 200
    assert _download(world, report_type="MONTHLY", year=2025, month=3, scope="department", department_id=str(world.hr_dept.id)).status_code == 403
    assert _download(world, report_type="MONTHLY", year=2025, month=3, scope="company").status_code == 403

    # Revoke -> denied again.
    world.actor["user"] = world.sa
    assert world.client.post(f"/api/v1/system/report-downloads/grants/{grant_id}/revoke").status_code == 200
    world.actor["user"] = world.hr
    assert _download(world, report_type="YEARLY", year=2025, scope="department", department_id=str(world.sales.id)).status_code == 403

    # A company-wide grant opens company downloads too.
    world.actor["user"] = world.sa
    world.client.post(
        "/api/v1/system/report-downloads/grants",
        json={"user_id": str(world.hr.id), "scope_type": "COMPANY"},
    )
    world.actor["user"] = world.hr
    assert _download(world, report_type="MONTHLY", year=2025, month=3, scope="company").status_code == 200

    actions = {
        row.action
        for row in world.session.execute(select(AuditLog)).scalars().all()
    }
    assert {"REPORT_GRANT_CREATED", "REPORT_GRANT_REVOKED", "REPORT_DOWNLOAD"} <= actions

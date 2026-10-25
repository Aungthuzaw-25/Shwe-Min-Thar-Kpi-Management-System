"""Retention Stage 2 acceptance — lifecycle: offboard, the year
lock, hard delete keeping history, departed identities, Relink,
re-hire, and the December reminder job.

Fixture story: employee E leaves in 2025 with KPI records and a
completed task. Company/department totals must be identical before
offboarding, after offboarding, and after the (unlocked) hard
delete — with E's rows still attributable by snapshot + registry.
"""

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.core.dependencies import get_current_user
from app.database.connection import Base, get_db
from app.models.audit_log import AuditLog
from app.models.departed_identity import DepartedIdentity
from app.models.report_download import ReportDownloadGrant
from app.modules.departments.models import Department
from app.modules.kpi.models import KPIRecord, KPIStatus, PeriodType
from app.modules.kpi.repo import get_kpi_report_data
from app.modules.notifications.models import Notification
from app.modules.targets.models import Target
from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.users.models import Role, User


def _user(role, email, first="A", last="B"):
    return User(
        id=uuid.uuid4(), email=email, hashed_password="x",
        first_name=first, last_name=last, role=role, is_active=True,
    )


def _record(user_id, dept_id, start, score, *, name=None, dept_name=None):
    return KPIRecord(
        user_id=user_id, department_id=dept_id,
        period_type=PeriodType.MONTHLY,
        period_start=datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
        period_end=datetime(start.year, start.month, 28, tzinfo=timezone.utc),
        weighted_score=score, raw_score=score, percentage=score * 20,
        status_bucket=KPIStatus.GOOD,
        employee_name=name, department_name=dept_name,
    )


@pytest.fixture()
def world(tmp_path):
    db_file = tmp_path / "retention.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    session = sessionmaker(sync_engine)()
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    sa_role = Role(name="Super Admin")
    admin_role = Role(name="Admin")
    staff_role = Role(name="Employee")
    sales = Department(name="Sales")
    session.add_all([sa_role, admin_role, staff_role, sales])
    session.flush()
    sa = _user(sa_role, "sa@x.com", "Super", "Admin")
    admin = _user(admin_role, "admin@x.com", "Ann", "Admin")
    e = _user(staff_role, "e@x.com", "Ko", "Departed")
    f = _user(staff_role, "f@x.com", "Ma", "Stayed")
    session.add_all([sa, admin, e, f])
    session.flush()
    # E: two 2025 records + one completed task. F: one 2025 record.
    session.add_all([
        _record(e.id, sales.id, date(2025, 3, 1), 4.0),
        _record(e.id, sales.id, date(2025, 4, 1), 2.0),
        _record(f.id, sales.id, date(2025, 3, 1), 3.0),
    ])
    task = Task(
        title="E's finished task", owner_id=e.id,
        status=TaskStatus.COMPLETED,
    )
    session.add(task)
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
        session=session, factory=factory, client=client, actor=actor,
        sa=sa, admin=admin, e=e, f=f, sales=sales, task=task,
    )
    main_module.app.dependency_overrides.pop(get_db, None)
    main_module.app.dependency_overrides.pop(get_current_user, None)
    session.close()


async def _report_total(factory):
    async with factory() as db:
        rows = await get_kpi_report_data(db, "MONTHLY")
    return sum(r["total_score"] for r in rows), len(rows)


def _download_full_year(world, year):
    response = world.client.get(
        "/api/v1/system/report-download",
        params={"report_type": "YEARLY", "year": year, "scope": "company"},
    )
    assert response.status_code == 200, response.text


def _unlock_year(world, year=2025):
    """SA downloads the full-year company report + confirms backup."""
    _download_full_year(world, year)
    response = world.client.post(
        "/api/v1/system/retention/backup-confirm", json={"year": year}
    )
    assert response.status_code == 200, response.text
    assert response.json()["unlocked"] is True


def test_offboard_stamps_history_kills_login_and_ends_grants(world):
    # E holds a download grant; offboarding must end it.
    world.session.add(ReportDownloadGrant(
        user_id=world.e.id, scope_type="COMPANY", is_active=True,
    ))
    world.session.commit()

    response = world.client.post(
        f"/api/v1/users/{world.e.id}/offboard",
        json={"status": "RESIGNED", "resigned_at": "2025-06-15", "reason": "New job"},
    )
    assert response.status_code == 200, response.text

    world.session.expire_all()
    user = world.session.get(User, world.e.id)
    assert user.status == "RESIGNED"
    assert user.resigned_at == date(2025, 6, 15)
    assert user.is_active is False and user.is_deleted is True
    assert user.email.startswith("deleted_")
    record = world.session.execute(
        select(KPIRecord).where(KPIRecord.user_id == world.e.id)
    ).scalars().first()
    assert record.employee_resigned_at == date(2025, 6, 15)
    assert record.employee_name == "Ko Departed"
    task = world.session.get(Task, world.task.id)
    assert task.owner_resigned_at == date(2025, 6, 15)
    assert task.owner_name == "Ko Departed"
    grant = world.session.execute(select(ReportDownloadGrant)).scalars().first()
    assert grant.is_active is False and grant.revoked_by == world.sa.id
    audit = world.session.execute(
        select(AuditLog).where(AuditLog.action == "USER_OFFBOARDED")
    ).scalars().first()
    assert audit is not None and audit.new_value["reason"] == "New job"


def test_hard_delete_lock_sequence_and_history_survival(world):
    import asyncio

    before = asyncio.run(_report_total(world.factory))
    assert before == (9.0, 3)

    world.client.post(
        f"/api/v1/users/{world.e.id}/offboard",
        json={"status": "RESIGNED", "resigned_at": "2025-06-15"},
    )
    # Totals identical right after offboarding.
    assert asyncio.run(_report_total(world.factory)) == before

    # Lock step 1: no full-year download yet.
    response = world.client.delete(f"/api/v1/users/{world.e.id}/permanent")
    assert response.status_code == 400
    assert "full-year report for 2025" in response.json()["detail"]

    # A monthly download does NOT satisfy the lock.
    world.client.get(
        "/api/v1/system/report-download",
        params={"report_type": "MONTHLY", "year": 2025, "month": 3, "scope": "company"},
    )
    response = world.client.delete(f"/api/v1/users/{world.e.id}/permanent")
    assert response.status_code == 400
    assert "full-year report for 2025" in response.json()["detail"]

    _unlock_year(world)

    # Decided scope: a target E created does NOT block — it stays,
    # detached (created_by NULL) after the delete.
    target = Target(
        name="E target", created_by_id=world.e.id,
        department_id=world.sales.id, target_value=10,
        target_month="2025-06",
    )
    world.session.add(target)
    world.session.commit()
    target_id = target.id

    e_id = world.e.id
    response = world.client.delete(f"/api/v1/users/{e_id}/permanent")
    assert response.status_code == 200, response.text
    world.session.expire_all()
    kept_target = world.session.get(Target, target_id)
    assert kept_target is not None and kept_target.created_by_id is None

    world.session.expire_all()
    assert world.session.execute(
        select(User).where(User.id == e_id)
    ).scalars().first() is None
    kept = world.session.execute(
        select(KPIRecord).where(KPIRecord.employee_name == "Ko Departed")
    ).scalars().all()
    assert len(kept) == 2
    assert all(r.user_id is None for r in kept)
    assert all(r.departed_identity_id is not None for r in kept)
    task = world.session.get(Task, world.task.id)
    assert task is not None and task.owner_id is None
    identity = world.session.execute(select(DepartedIdentity)).scalars().first()
    assert identity is not None
    assert identity.email == "e@x.com"  # real email, rename prefix stripped
    assert identity.resigned_at == date(2025, 6, 15)
    # Totals identical after the account is gone.
    after_rows = asyncio.run(_report_total(world.factory))
    assert after_rows == before
    audit = world.session.execute(
        select(AuditLog).where(AuditLog.action == "USER_HARD_DELETED")
    ).scalars().first()
    assert audit is not None and audit.new_value["kpi_records_kept"] == 2


def test_relink_reattaches_records_and_tasks(world):
    world.client.post(
        f"/api/v1/users/{world.e.id}/offboard",
        json={"status": "RESIGNED", "resigned_at": "2025-06-15"},
    )
    _unlock_year(world)
    assert world.client.delete(f"/api/v1/users/{world.e.id}/permanent").status_code == 200

    listing = world.client.get("/api/v1/system/departed-identities")
    assert listing.status_code == 200
    (entry,) = listing.json()
    assert entry["kpi_records"] == 2 and entry["completed_tasks"] == 1
    assert entry["employee_name"] == "Ko Departed"

    new_user = _user(world.session.execute(select(Role).where(Role.name == "Employee")).scalars().first(), "e2@x.com", "Ko", "Departed")
    world.session.add(new_user)
    world.session.commit()

    # Admin cannot relink.
    world.actor["user"] = world.admin
    response = world.client.post(
        f"/api/v1/system/departed-identities/{entry['id']}/relink",
        json={"new_user_id": str(new_user.id)},
    )
    assert response.status_code == 403
    world.actor["user"] = world.sa

    response = world.client.post(
        f"/api/v1/system/departed-identities/{entry['id']}/relink",
        json={"new_user_id": str(new_user.id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["kpi_records_relinked"] == 2
    assert response.json()["tasks_relinked"] == 1

    world.session.expire_all()
    records = world.session.execute(
        select(KPIRecord).where(KPIRecord.user_id == new_user.id)
    ).scalars().all()
    assert len(records) == 2
    assert all(r.departed_identity_id is None for r in records)
    assert all(r.employee_resigned_at is None for r in records)  # no Resigned label anymore
    assert world.session.get(Task, world.task.id).owner_id == new_user.id
    assert world.session.execute(select(DepartedIdentity)).scalars().all() == []
    audit = world.session.execute(
        select(AuditLog).where(AuditLog.action == "IDENTITY_RELINKED")
    ).scalars().first()
    assert audit is not None and audit.new_value["kpi_records_relinked"] == 2


def test_year_not_ended_and_active_with_history_guards(world):
    # Resigned THIS year: the year has not ended -> locked.
    world.client.post(
        f"/api/v1/users/{world.e.id}/offboard",
        json={"status": "RESIGNED", "resigned_at": "2026-01-10"},
    )
    response = world.client.delete(f"/api/v1/users/{world.e.id}/permanent")
    assert response.status_code == 400
    assert "year 2026 has ended" in response.json()["detail"]

    # Active employee WITH history: must offboard first.
    response = world.client.delete(f"/api/v1/users/{world.f.id}/permanent")
    assert response.status_code == 400
    assert "Offboard them first" in response.json()["detail"]


def test_history_free_account_deletes_directly(world):
    ghost = _user(world.session.execute(select(Role).where(Role.name == "Employee")).scalars().first(), "ghost@x.com", "Test", "Account")
    world.session.add(ghost)
    world.session.commit()
    ghost_id = ghost.id  # capture before the row goes (expiry would fail)
    response = world.client.delete(f"/api/v1/users/{ghost_id}/permanent")
    assert response.status_code == 200, response.text
    world.session.expire_all()
    assert world.session.execute(
        select(User).where(User.id == ghost_id)
    ).scalars().first() is None


def test_restore_is_a_rehire(world):
    world.client.post(
        f"/api/v1/users/{world.e.id}/offboard",
        json={"status": "RESIGNED", "resigned_at": "2025-06-15"},
    )
    response = world.client.post(f"/api/v1/users/{world.e.id}/restore")
    assert response.status_code == 200, response.text
    world.session.expire_all()
    user = world.session.get(User, world.e.id)
    assert user.status == "ACTIVE" and user.resigned_at is None
    assert user.is_deleted is False and user.is_active is True
    assert user.email == "e@x.com"
    audit = world.session.execute(
        select(AuditLog).where(AuditLog.action == "USER_REHIRED")
    ).scalars().first()
    assert audit is not None


def test_december_reminder_job(world, monkeypatch):
    import app.background_jobs.scheduler as scheduler_module
    import app.database.connection as connection_module

    monkeypatch.setattr(connection_module, "AsyncSessionLocal", world.factory)

    import asyncio

    dec1 = datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc)
    created = asyncio.run(scheduler_module.run_yearly_report_reminder_job(now=dec1))
    assert created == 1  # only the active Super Admin
    notes = world.session.execute(select(Notification)).scalars().all()
    assert len(notes) == 1 and notes[0].user_id == world.sa.id
    assert notes[0].notification_type == "YEARLY_REPORT_REMINDER"

    # Once the full-year download exists, the reminders stop.
    _download_full_year(world, 2026)
    created = asyncio.run(
        scheduler_module.run_yearly_report_reminder_job(
            now=datetime(2026, 12, 8, 9, 0, tzinfo=timezone.utc)
        )
    )
    assert created == 0
    # And outside the reminder days nothing is created.
    created = asyncio.run(
        scheduler_module.run_yearly_report_reminder_job(
            now=datetime(2026, 11, 30, 9, 0, tzinfo=timezone.utc)
        )
    )
    assert created == 0


def test_ranking_year_view_includes_departed(world):
    world.client.post(
        f"/api/v1/users/{world.e.id}/offboard",
        json={"status": "RESIGNED", "resigned_at": "2025-06-15"},
    )
    response = world.client.get(
        "/api/v1/kpi/dashboard/ranking",
        params={"period_type": "MONTHLY", "year": 2025, "limit": 10},
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    names = {row["user_name"]: row for row in rows}
    assert "Ko Departed" in names  # departed, still on the year's board
    assert names["Ko Departed"]["is_departed"] is True
    assert names["Ko Departed"]["resigned_at"] == "2025-06-15"
    assert "Ma Stayed" in names


def test_hard_delete_purges_task_detail_activity_and_files(world, monkeypatch):
    """Decided scope: kept tasks stay at snapshot level — evidence
    rows AND files, histories, and own-task reviews go with the
    account, the person's activity timeline goes too, and process
    rows (batches) stay detached."""
    from app.modules.tasks.models.activity_timeline import ActivityTimeline
    from app.modules.tasks.models.submission_batch import SubmissionBatch
    from app.modules.tasks.models.task_evidence import TaskEvidence
    from app.modules.tasks.models.task_history import TaskHistory
    from app.modules.tasks.models.task_review import TaskReview

    session = world.session
    e_id = world.e.id  # capture now; the row will be gone later
    session.add_all([
        TaskEvidence(task_id=world.task.id, file_name="proof.png",
                     file_path="evidence/proof.png", file_hash="h" * 64,
                     uploaded_by=world.f.id),
        TaskHistory(task_id=world.task.id, changed_by=world.f.id,
                    action="STATUS_CHANGED", old_value="OPEN", new_value="COMPLETED"),
        TaskReview(task_id=world.task.id, reviewer_id=world.f.id,
                   reviewer_level="L1", action="APPROVED"),
        ActivityTimeline(user_id=world.e.id, action="TASK_UPDATED",
                         entity_type="task", entity_id=world.task.id,
                         description="daily activity"),
    ])
    session.commit()

    deleted_files = []

    async def fake_delete_file(path):
        deleted_files.append(path)

    monkeypatch.setattr(
        "app.services.storage_service.storage_service.delete_file",
        fake_delete_file,
    )

    world.client.post(
        f"/api/v1/users/{world.e.id}/offboard",
        json={"status": "RESIGNED", "resigned_at": "2025-06-15"},
    )
    _unlock_year(world)

    # A submitted batch does NOT block: the process row stays,
    # detached (submitter NULL), so KPI provenance survives.
    batch = SubmissionBatch(submitter_id=world.e.id)
    session.add(batch)
    session.commit()
    batch_id = batch.id

    response = world.client.delete(f"/api/v1/users/{world.e.id}/permanent")
    assert response.status_code == 200, response.text
    session.expire_all()
    kept_batch = session.get(SubmissionBatch, batch_id)
    assert kept_batch is not None and kept_batch.submitter_id is None

    session.expire_all()
    # The task itself stays (snapshot level); its detail is gone.
    assert session.get(Task, world.task.id) is not None
    assert session.execute(select(TaskEvidence)).scalars().all() == []
    assert session.execute(select(TaskHistory)).scalars().all() == []
    assert session.execute(select(TaskReview)).scalars().all() == []
    assert session.execute(
        select(ActivityTimeline).where(ActivityTimeline.user_id == e_id)
    ).scalars().all() == []
    # The stored file was deleted too — no orphan in storage.
    assert deleted_files == ["evidence/proof.png"]
    audit = session.execute(
        select(AuditLog).where(AuditLog.action == "USER_HARD_DELETED")
    ).scalars().first()
    assert audit.new_value["evidence_files_removed"] == 1


def test_outcome_history_survives_trail_purge_and_is_viewable(world):
    """Zenn's line: outcomes stay, activity trails go. A completed
    task whose completion facts live ONLY in the history/review
    trail must still answer 'what did the previous person finish?'
    after the hard delete — through the Departed view."""
    from datetime import datetime as _dt

    from app.modules.tasks.models.task_history import TaskHistory
    from app.modules.tasks.models.task_review import TaskReview

    session = world.session
    done_task = Task(
        title="Q2 branch audit", owner_id=world.e.id,
        status=TaskStatus.COMPLETED,
    )
    session.add(done_task)
    session.flush()
    session.add_all([
        TaskHistory(
            task_id=done_task.id, changed_by=world.f.id,
            action="STATUS_CHANGED", old_value="IN_PROGRESS",
            new_value="Completed", remarks="Finished early",
            created_at=_dt(2025, 5, 20, 10, 30),
        ),
        TaskReview(
            task_id=done_task.id, reviewer_id=world.f.id,
            reviewer_level="L1", action="APPROVED",
            comments="Delivered well", created_at=_dt(2025, 5, 20, 11, 0),
        ),
    ])
    session.commit()
    done_task_id = done_task.id

    world.client.post(
        f"/api/v1/users/{world.e.id}/offboard",
        json={"status": "RESIGNED", "resigned_at": "2025-06-15"},
    )
    _unlock_year(world)
    assert world.client.delete(f"/api/v1/users/{world.e.id}/permanent").status_code == 200

    session.expire_all()
    kept = session.get(Task, done_task_id)
    assert kept is not None and kept.owner_id is None
    # Outcome facts were copied onto the row before the trail went.
    assert kept.outcome_snapshot is not None
    assert kept.outcome_snapshot["completed_at"].startswith("2025-05-20")
    assert "Delivered well" in kept.outcome_snapshot["result"]
    # The trail itself is gone.
    assert session.execute(
        select(TaskHistory).where(TaskHistory.task_id == done_task_id)
    ).scalars().all() == []
    assert session.execute(
        select(TaskReview).where(TaskReview.task_id == done_task_id)
    ).scalars().all() == []

    # The handover view answers "what did they finish?" — SA only.
    identity = session.execute(select(DepartedIdentity)).scalars().first()
    response = world.client.get(f"/api/v1/system/departed-identities/{identity.id}/tasks")
    assert response.status_code == 200, response.text
    rows = {row["title"]: row for row in response.json()}
    assert rows["Q2 branch audit"]["status"] == "Completed"
    assert rows["Q2 branch audit"]["completed_at"].startswith("2025-05-20")
    assert "Delivered well" in rows["Q2 branch audit"]["result"]
    assert rows["Q2 branch audit"]["owner_name"] == "Ko Departed"
    world.actor["user"] = world.admin
    assert world.client.get(f"/api/v1/system/departed-identities/{identity.id}/tasks").status_code == 403


def test_task_snapshots_purge_after_one_year_kpi_records_untouched(world):
    """Zenn's keep-period: snapshot task rows live 1 year after the
    hard delete, then the purge removes them — KPI records/history
    are never touched, and the purge itself leaves an audit row."""
    import asyncio
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from app.modules.system import retention_service as retention

    session = world.session
    e_id = world.e.id  # capture now; the row will be gone later
    world.client.post(
        f"/api/v1/users/{e_id}/offboard",
        json={"status": "RESIGNED", "resigned_at": "2025-06-15"},
    )
    _unlock_year(world)
    assert world.client.delete(f"/api/v1/users/{e_id}/permanent").status_code == 200
    session.expire_all()
    identity = session.execute(select(DepartedIdentity)).scalars().first()
    record_id_kept = session.execute(
        select(KPIRecord.id).where(KPIRecord.departed_identity_id == identity.id)
    ).scalars().first()
    assert record_id_kept is not None

    async def _purge(today):
        # A fresh engine per call: the shared fixture engine's
        # connection is bound to whichever event loop touched it
        # first, which made this test flaky in the full suite.
        from sqlalchemy.ext.asyncio import async_sessionmaker as _asm
        from sqlalchemy.ext.asyncio import create_async_engine as _cae

        engine = _cae(f"sqlite+aiosqlite:///{world.session.bind.url.database}")
        try:
            async with _asm(engine, expire_on_commit=False)() as db:
                return await retention.purge_expired_task_snapshots(db, today=today)
        finally:
            await engine.dispose()

    # Fresh hard delete: nothing is old enough yet.
    today = _dt.now().date()
    assert asyncio.run(_purge(today)) == []
    assert session.execute(
        select(Task).where(Task.departed_identity_id == identity.id)
    ).scalars().all() != []

    # Shift the hard-delete moment 400 days into the past (fixture
    # time travel on the surviving audit row), then purge "today".
    audit = session.execute(
        select(AuditLog).where(AuditLog.action == "USER_HARD_DELETED")
    ).scalars().first()
    audit.created_at = _dt.now() - _td(days=400)
    session.commit()

    purged = asyncio.run(_purge(today))
    assert len(purged) == 1 and purged[0]["tasks_removed"] >= 1
    session.expire_all()
    assert session.execute(
        select(Task).where(Task.departed_identity_id == identity.id)
    ).scalars().all() == []
    # KPI records + history: untouched, still linked to the identity.
    kept_record = session.get(KPIRecord, record_id_kept)
    assert kept_record is not None and kept_record.departed_identity_id == identity.id
    # The purge left its own audit row, and a re-run is a no-op.
    purge_audit = session.execute(
        select(AuditLog).where(AuditLog.action == "TASK_SNAPSHOTS_PURGED")
    ).scalars().all()
    assert len(purge_audit) == 1
    assert purge_audit[0].new_value["tasks_removed"] >= 1
    assert asyncio.run(_purge(today)) == []


def test_hard_delete_detaches_all_five_raw_sql_columns(world):
    """QA Gate A regression: five references once detached through
    raw text() SQL with str(user_id) params silently matched zero
    rows — department head, delegation, task assigner, formula
    creator, action-plan reviewer must ALL be NULL afterwards,
    with the referencing rows themselves kept."""
    from datetime import datetime as _dt

    from app.modules.departments.models import Department
    from app.modules.kpi.models import ActionPlan, KPIFormula
    from app.modules.users.models import User as UserModel

    session = world.session
    e_id, f_id = world.e.id, world.f.id
    ops = Department(name="Ops", head_id=e_id)
    f_record = session.execute(
        select(KPIRecord).where(KPIRecord.user_id == f_id)
    ).scalars().first()
    f_user = session.get(UserModel, f_id)
    f_user.delegated_to_id = e_id
    f_task = Task(title="F's task", owner_id=f_id, assigner_id=e_id)
    formula = KPIFormula(
        name="E-made formula", department_id=world.sales.id,
        user_level="L2", categories=[], created_by_id=e_id,
    )
    plan = ActionPlan(
        kpi_record_id=f_record.id, user_id=f_id,
        description="Improve", target_completion_date=_dt(2025, 12, 31),
        reviewed_by_id=e_id,
    )
    session.add_all([ops, f_task, formula, plan])
    session.commit()
    ops_id, f_task_id, formula_id, plan_id = ops.id, f_task.id, formula.id, plan.id

    world.client.post(
        f"/api/v1/users/{e_id}/offboard",
        json={"status": "RESIGNED", "resigned_at": "2025-06-15"},
    )
    _unlock_year(world)
    assert world.client.delete(f"/api/v1/users/{e_id}/permanent").status_code == 200

    session.expire_all()
    assert session.get(Department, ops_id).head_id is None
    assert session.get(UserModel, f_id).delegated_to_id is None
    assert session.get(Task, f_task_id).assigner_id is None
    assert session.get(KPIFormula, formula_id).created_by_id is None
    assert session.get(ActionPlan, plan_id).reviewed_by_id is None

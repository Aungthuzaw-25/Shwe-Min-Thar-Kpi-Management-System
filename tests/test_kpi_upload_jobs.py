"""KPI upload background jobs (F6) — design §8 cases 1–12.

The POST no longer runs the engine: it stages the file, inserts a
QUEUED job and returns 202. The scheduler pickup claims one job at
a time and runs it to completion. These tests prove the receipt,
the worker's all-or-nothing transaction, the idempotency replay,
the owner/SA visibility rules, the stuck-job reclaim, serial
execution, the prune, and that notices only ever fire after the
engine transaction committed.

Harness notes (same pattern as test_kpi_upload_roundtrip.py):
- File SQLite world, SUBSET table list (+ kpi_upload_jobs,
  idempotency_keys, permissions, role_permissions).
- The fake storage stands in for MinIO (the CI env has no S3).
- The worker + idempotency guard open their own sessions via the
  app-global AsyncSessionLocal — both are patched to this world's
  session factory, and the test app mounts the idempotency
  middleware + replay handler (as main.py does).
"""

import asyncio
import hashlib
import io
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook
from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core import idempotency as idempotency_module
from app.core.dependencies import get_current_user
from app.core.idempotency import (
    REPLAYED_HEADER,
    IdempotentReplay,
    IdempotencyCompletionMiddleware,
)
from app.core.timezone import utc_now_naive
from app.database.connection import Base, get_db
from app.models.idempotency import IdempotencyKey
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi import upload_jobs as upload_jobs_module
from app.modules.kpi import upload_router as upload_router_module
from app.modules.kpi.engine import KPICalculationEngine
from app.modules.kpi.models import (
    FormulaScope,
    FormulaStatus,
    KPIAssignment,
    KPIFormula,
    KPIFormulaPeriod,
    KPIHistory,
    KPIPeriod,
    KPIPeriodStatus,
    KPIRecord,
    KPIScoringRuleItem,
    KpiUploadJob,
    KpiUploadJobStatus,
    PeriodType,
)
from app.modules.kpi.period_ranges import calendar_period_range
from app.modules.kpi.upload_jobs import (
    _reclaim_stuck_jobs,
    prune_kpi_upload_jobs,
    run_kpi_upload_pickup,
)
from app.modules.kpi.upload_router import limiter, router
from app.modules.tasks.models.task import Task
from app.modules.tasks.models.task_review import TaskReview
from app.modules.users.models import (
    Permission,
    Position,
    Role,
    RolePermission,
    User,
    UserPermissionOverride,
)

TABLES = [
    Department.__table__,
    UserDepartment.__table__,
    Role.__table__,
    Position.__table__,
    User.__table__,
    KPIFormula.__table__,
    KPIFormulaPeriod.__table__,
    KPIPeriod.__table__,
    KPIAssignment.__table__,
    KPIRecord.__table__,
    KPIHistory.__table__,
    KPIScoringRuleItem.__table__,
    Task.__table__,
    TaskReview.__table__,
    KpiUploadJob.__table__,
    IdempotencyKey.__table__,
    Permission.__table__,
    RolePermission.__table__,
    UserPermissionOverride.__table__,
]


class FakeStorage:
    """In-memory MinIO stand-in: key -> (bytes, last_modified)."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, datetime]] = {}

    async def upload_file(self, file, folder="evidence", *, content_type=None):
        await file.seek(0)
        data = await file.read()
        extension = (file.filename or "").rsplit(".", 1)[-1].lower() or "bin"
        key = f"{folder}/{uuid4()}.{extension}"
        self.objects[key] = (data, datetime.now(UTC))
        return key

    async def download_file(self, object_name):
        if object_name not in self.objects:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Staged file no longer exists.")
        return self.objects[object_name][0]

    async def delete_file(self, object_name):
        self.objects.pop(object_name, None)

    def list_object_keys(self, prefix):
        return [
            (key, last_modified)
            for key, (_data, last_modified) in self.objects.items()
            if key.startswith(prefix)
        ]


async def _setup(tmp_path, employee_count=2):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'kpi-upload-jobs.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection, tables=TABLES
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        department = Department(name=f"Upload department {uuid4()}")
        admin_role = Role(name="Super Admin")
        employee_role = Role(name=f"Employee {uuid4()}")
        uploader_role = Role(name=f"Uploader {uuid4()}")
        create_permission = Permission(code="kpi.create", description="Upload KPI data")
        db.add_all([department, admin_role, employee_role, uploader_role, create_permission])
        await db.flush()
        db.add(RolePermission(role_id=uploader_role.id, permission_id=create_permission.id))
        await db.flush()

        position = Position(name=f"Upload position {uuid4()}", department_id=department.id)
        admin = User(
            email=f"admin-{uuid4()}@example.test",
            hashed_password="hashed",
            first_name="QA",
            last_name="Admin",
            role_id=admin_role.id,
            role=admin_role,
            is_active=True,
            is_deleted=False,
        )
        uploader = User(
            email=f"uploader-{uuid4()}@example.test",
            hashed_password="hashed",
            first_name="QA",
            last_name="Uploader",
            role_id=uploader_role.id,
            role=uploader_role,
            # No position on purpose: the uploader has department
            # SCOPE (via the membership below) so they can upload the
            # employees' rows, but no formula matches a position-less
            # user — so they never appear as a row in the template.
            is_active=True,
            is_deleted=False,
            user_departments=[
                UserDepartment(
                    department_id=department.id,
                    user_level="L2",
                    is_deleted=False,
                )
            ],
        )
        employees = [
            User(
                email=f"employee-{index}-{uuid4()}@example.test",
                hashed_password="hashed",
                first_name="QA",
                last_name=f"Employee {index}",
                role_id=employee_role.id,
                position=position,
                is_active=True,
                is_deleted=False,
                user_departments=[
                    UserDepartment(
                        department_id=department.id,
                        user_level="L2",
                        is_deleted=False,
                    )
                ],
            )
            for index in range(1, employee_count + 1)
        ]
        db.add_all([position, admin, uploader, *employees])
        await db.flush()

        current_month = date.today().replace(day=1)
        period_range = calendar_period_range(PeriodType.MONTHLY, current_month)
        period = KPIPeriod(
            label=f"Upload test {current_month:%Y-%m}",
            start_date=period_range.start_inclusive.astimezone(UTC).replace(tzinfo=None),
            end_date=period_range.end_exclusive.astimezone(UTC).replace(tzinfo=None),
            status=KPIPeriodStatus.OPEN,
            is_deleted=False,
        )
        formula = KPIFormula(
            name="Upload regression formula",
            department_id=department.id,
            position_name=position.name,
            position_id=position.id,
            user_level="L2",
            categories=[
                {
                    "name": "Attendance",
                    "weight": 1.0,
                    "max_score": 5.0,
                    "evaluation_type": "DEDUCTION",
                    "deductions": [
                        {
                            "name": "Late Arrival",
                            "measure_type": "count",
                            "rules": [{"operator": ">=", "value": 2, "deduct_score": 2}],
                        }
                    ],
                }
            ],
            scope=FormulaScope.DEPARTMENT,
            is_active=True,
            status=FormulaStatus.ACTIVE,
            effective_month=current_month.strftime("%Y-%m"),
            version=1,
            is_deleted=False,
            scoring_rules=[
                KPIScoringRuleItem(
                    category_name="attendance",
                    rule_name="late arrival",
                    measure_type="count",
                    operator=">=",
                    target_value=2,
                    deduct_score=2,
                )
            ],
        )
        db.add_all([period, formula])
        await db.commit()
        return engine, session_factory, admin, uploader, employees, period, formula


async def _client(session_factory, user):
    app = FastAPI()

    def _replay_handler(request, exc):
        assert isinstance(exc, IdempotentReplay)
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.body,
            headers={REPLAYED_HEADER: "true"},
        )

    app.add_exception_handler(IdempotentReplay, _replay_handler)
    app.add_middleware(IdempotencyCompletionMiddleware)
    app.include_router(router, prefix="/api/v1")
    app.state.limiter = limiter
    app.state.limiter.enabled = False

    async def override_db():
        async with session_factory() as db:
            yield db

    # The user the endpoint sees must carry its role (+ the role's
    # permissions for the non-SA uploader), as get_current_user does.
    loaded = (
        await _load_user(session_factory, user.id)
    )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: loaded
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _load_user(session_factory, user_id):
    async with session_factory() as db:
        return (
            await db.execute(
                select(User)
                .where(User.id == user_id)
                .options(
                    selectinload(User.role).selectinload(Role.permissions),
                    selectinload(User.position),
                    selectinload(User.user_departments),
                    # has_effective_permission reads the overrides of
                    # every non-SA user — they must be loaded, or the
                    # detached instance cannot lazy-load them.
                    selectinload(User.permission_overrides).selectinload(
                        UserPermissionOverride.permission
                    ),
                )
            )
        ).scalars().first()


async def _template(client, period_id):
    response = await client.get(
        "/api/v1/kpi-uploads/export-template",
        params={"period_id": str(period_id)},
    )
    assert response.status_code == 200, response.text
    return response.content


def _workbook_bytes(content, edit):
    workbook = load_workbook(io.BytesIO(content))
    worksheet = workbook["KPI_Data"]
    edit(workbook, worksheet)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _fill_metrics(worksheet, values):
    for row_index, metric in enumerate(values, start=2):
        worksheet.cell(row_index, 6, metric)


async def _upload(client, content, period_id, filename="KPI_Master_Template.xlsx", headers=None):
    return await client.post(
        "/api/v1/kpi-uploads/monthly-data",
        data={"period_id": str(period_id)} if period_id else {},
        files={
            "file": (
                filename,
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=headers or {},
    )


def _patch_world(session_factory, storage):
    """Point the worker, the router and the idempotency layer at
    this world's session factory + fake storage."""
    return (
        patch.object(upload_jobs_module, "AsyncSessionLocal", session_factory),
        patch.object(upload_jobs_module, "storage_service", storage),
        patch.object(upload_router_module, "storage_service", storage),
        patch.object(idempotency_module, "AsyncSessionLocal", session_factory),
    )


def _valid_workbook(client_content, values=(1.25, "2")):
    return _workbook_bytes(client_content, lambda _wb, ws: _fill_metrics(ws, values))


async def _enqueue(client, content, period_id, headers=None):
    response = await _upload(client, content, period_id, headers=headers)
    assert response.status_code == 202, response.text
    return response.json()


async def _job_row(session_factory, job_id):
    async with session_factory() as db:
        return await db.get(KpiUploadJob, UUID(str(job_id)))


def test_enqueue_valid_returns_202_and_queues_job(tmp_path):
    """Case 1: valid file → 202 + QUEUED + rows_total + staged object."""

    async def scenario():
        engine, factory, admin, _uploader, _employees, period, _formula = await _setup(tmp_path)
        storage = FakeStorage()
        patches = _patch_world(factory, storage)
        try:
            for p in patches:
                p.start()
            async with await _client(factory, admin) as client:
                content = await _template(client, period.id)
                payload = _valid_workbook(content)
                body = await _enqueue(client, payload, period.id)
                assert body["status"] == "queued"
                assert body["rows_total"] == 2
                assert body["period_label"] == period.label
                assert body["status_url"] == (
                    f"/api/v1/kpi-uploads/monthly-data/jobs/{body['job_id']}"
                )
                job = await _job_row(factory, body["job_id"])
                assert job.status == KpiUploadJobStatus.QUEUED.value
                assert job.owner_id == admin.id
                assert job.owner_name == "QA Admin"
                assert job.period_id == period.id
                assert job.rows_total == 2
                assert job.rows_processed == 0
                assert job.content_hash == hashlib.sha256(payload).hexdigest()
                assert job.source_object_key in storage.objects
                assert storage.objects[job.source_object_key][0] == payload
        finally:
            for p in patches:
                p.stop()
            limiter.enabled = True
            await engine.dispose()

    asyncio.run(scenario())


def test_invalid_upload_creates_no_job_and_stages_nothing(tmp_path):
    """Case 2: invalid file → 400, NO job row, NO staged object."""

    async def scenario():
        engine, factory, admin, _uploader, _employees, period, _formula = await _setup(tmp_path)
        storage = FakeStorage()
        patches = _patch_world(factory, storage)
        try:
            for p in patches:
                p.start()
            async with await _client(factory, admin) as client:
                content = await _template(client, period.id)
                # A workbook with no data rows at all.
                payload = _workbook_bytes(content, lambda _wb, _ws: None)
                response = await _upload(client, payload, period.id)
                assert response.status_code == 400, response.text
                assert response.json()["detail"]["code"] == "validation_error"
                async with factory() as db:
                    count = (
                        await db.execute(
                            select(func.count()).select_from(KpiUploadJob)
                        )
                    ).scalar_one()
                assert count == 0
                assert storage.objects == {}
        finally:
            for p in patches:
                p.stop()
            limiter.enabled = True
            await engine.dispose()

    asyncio.run(scenario())


def test_poll_transitions_queued_running_succeeded(tmp_path):
    """Case 3: QUEUED → RUNNING → SUCCEEDED, rows_processed climbs
    to rows_total (12 rows: progress fires at row 10)."""

    async def scenario():
        engine, factory, admin, _uploader, _employees, period, _formula = await _setup(
            tmp_path, employee_count=12
        )
        storage = FakeStorage()
        patches = _patch_world(factory, storage)
        progress_calls: list[int] = []
        flush_calls: list[dict] = []

        async def recording_flush(db, pending, *, notifier=None):
            async with factory() as check_db:
                visible = (
                    await check_db.execute(
                        select(func.count()).select_from(KPIRecord)
                    )
                ).scalar_one()
            flush_calls.append({"pending": list(pending), "records_visible": visible})
            return []

        async def recording_progress(job_id, rows_processed):
            progress_calls.append(rows_processed)

        try:
            for p in patches:
                p.start()
            with (
                patch.object(upload_jobs_module, "flush_pending_notifications", recording_flush),
                patch.object(upload_jobs_module, "_set_job_progress", recording_progress),
            ):
                async with await _client(factory, admin) as client:
                    content = await _template(client, period.id)
                    payload = _valid_workbook(content, values=[1] * 12)
                    body = await _enqueue(client, payload, period.id)
                    job_id = body["job_id"]

                    detail = await client.get(f"/api/v1/kpi-uploads/monthly-data/jobs/{job_id}")
                    assert detail.status_code == 200
                    assert detail.json()["status"] == "queued"
                    assert detail.json()["rows_processed"] == 0

                    # The claim itself moves QUEUED → RUNNING.
                    async with factory() as db:
                        claimed = await upload_jobs_module._claim_oldest_queued_job(
                            db, now=utc_now_naive()
                        )
                    assert str(claimed) == job_id
                    running = await _job_row(factory, job_id)
                    assert running.status == KpiUploadJobStatus.RUNNING.value
                    assert running.attempt_count == 1
                    assert running.started_at is not None

                    # Reset to QUEUED so the pickup runs it end to end.
                    async with factory() as db:
                        job = await db.get(KpiUploadJob, UUID(job_id))
                        job.status = KpiUploadJobStatus.QUEUED.value
                        job.started_at = None
                        job.last_heartbeat_at = None
                        job.attempt_count = 0
                        await db.commit()

                    processed = await run_kpi_upload_pickup()
                    assert str(processed) == job_id
                    detail = await client.get(f"/api/v1/kpi-uploads/monthly-data/jobs/{job_id}")
                    assert detail.status_code == 200
                    assert detail.json()["status"] == "succeeded"
                    assert detail.json()["rows_total"] == 12
                    assert detail.json()["rows_processed"] == 12
                    assert detail.json()["finished_at"] is not None

                    # Case 12 (success half): the flush ran once, after
                    # the commit — all 12 records were visible to it.
                    assert len(flush_calls) == 1
                    assert flush_calls[0]["records_visible"] == 12
                    assert progress_calls == [10]
        finally:
            for p in patches:
                p.stop()
            limiter.enabled = True
            await engine.dispose()

    asyncio.run(scenario())


def test_engine_failure_rolls_back_everything_and_reports_failed(tmp_path):
    """Case 4: the engine fails mid-file → zero records, zero
    history, zero notices, FAILED with today's processing_error —
    and the status SURVIVES the engine rollback (the key test)."""

    async def scenario():
        engine, factory, admin, _uploader, _employees, period, _formula = await _setup(tmp_path)
        storage = FakeStorage()
        patches = _patch_world(factory, storage)
        flush_calls: list[dict] = []

        async def recording_flush(db, pending, *, notifier=None):
            flush_calls.append({"pending": list(pending)})
            return []

        calls = {"n": 0}
        real_calculate = KPICalculationEngine.execute_kpi_calculation

        async def failing_calculate(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom mid-file")
            return await real_calculate(*args, **kwargs)

        try:
            for p in patches:
                p.start()
            with (
                patch.object(upload_jobs_module, "flush_pending_notifications", recording_flush),
                patch.object(KPICalculationEngine, "execute_kpi_calculation", failing_calculate),
            ):
                async with await _client(factory, admin) as client:
                    content = await _template(client, period.id)
                    payload = _valid_workbook(content)
                    body = await _enqueue(client, payload, period.id)
                    processed = await run_kpi_upload_pickup()
                    assert str(processed) == body["job_id"]
                    detail = await client.get(
                        f"/api/v1/kpi-uploads/monthly-data/jobs/{body['job_id']}"
                    )
                    assert detail.json()["status"] == "failed"
                    assert detail.json()["error_code"] == "processing_error"
                    assert detail.json()["error_message"] == (
                        "The server could not process this file. Please try again."
                    )
                    assert detail.json()["finished_at"] is not None
                async with factory() as db:
                    records = (await db.execute(select(KPIRecord))).scalars().all()
                    history = (await db.execute(select(KPIHistory))).scalars().all()
                assert records == []
                assert history == []
                # Case 12 (failure half): no notice ever fired.
                assert flush_calls == []
        finally:
            for p in patches:
                p.stop()
            limiter.enabled = True
            await engine.dispose()

    asyncio.run(scenario())


def test_worker_revalidation_failure_reports_validation_error(tmp_path):
    """Case 5: the period is finalized between enqueue and pickup →
    FAILED/validation_error with the errors list."""

    async def scenario():
        engine, factory, admin, _uploader, _employees, period, _formula = await _setup(tmp_path)
        storage = FakeStorage()
        patches = _patch_world(factory, storage)
        try:
            for p in patches:
                p.start()
            async with await _client(factory, admin) as client:
                content = await _template(client, period.id)
                payload = _valid_workbook(content)
                body = await _enqueue(client, payload, period.id)
                async with factory() as db:
                    stored_period = await db.get(KPIPeriod, period.id)
                    stored_period.status = KPIPeriodStatus.FINALIZED
                    await db.commit()
                processed = await run_kpi_upload_pickup()
                assert str(processed) == body["job_id"]
                detail = await client.get(
                    f"/api/v1/kpi-uploads/monthly-data/jobs/{body['job_id']}"
                )
                assert detail.json()["status"] == "failed"
                assert detail.json()["error_code"] == "validation_error"
                assert detail.json()["error_detail"] == [
                    "The selected evaluation period is not open for KPI imports."
                ]
                async with factory() as db:
                    records = (await db.execute(select(KPIRecord))).scalars().all()
                assert records == []
        finally:
            for p in patches:
                p.stop()
            limiter.enabled = True
            await engine.dispose()

    asyncio.run(scenario())


def test_double_submit_same_key_returns_same_job(tmp_path):
    """Case 6: same Idempotency-Key + same file twice (the test
    client mints a fresh multipart boundary each time — the real
    browser retry) → the SAME job_id, ONE row, ONE staged object,
    and the second response is the replayed 202."""

    async def scenario():
        engine, factory, admin, _uploader, _employees, period, _formula = await _setup(tmp_path)
        storage = FakeStorage()
        patches = _patch_world(factory, storage)
        key = str(uuid4())
        try:
            for p in patches:
                p.start()
            async with await _client(factory, admin) as client:
                content = await _template(client, period.id)
                payload = _valid_workbook(content)
                first = await _upload(
                    client, payload, period.id, headers={"Idempotency-Key": key}
                )
                assert first.status_code == 202, first.text
                second = await _upload(
                    client, payload, period.id, headers={"Idempotency-Key": key}
                )
                assert second.status_code == 202, second.text
                assert second.headers.get(REPLAYED_HEADER) == "true"
                assert second.json()["job_id"] == first.json()["job_id"]
                async with factory() as db:
                    count = (
                        await db.execute(
                            select(func.count()).select_from(KpiUploadJob)
                        )
                    ).scalar_one()
                assert count == 1
                assert len(storage.objects) == 1
        finally:
            for p in patches:
                p.stop()
            limiter.enabled = True
            await engine.dispose()

    asyncio.run(scenario())


def test_other_users_job_is_404(tmp_path):
    """Case 7: a non-owner, non-SA uploader gets 404 on the detail
    endpoint and sees nothing in the list."""

    async def scenario():
        engine, factory, admin, uploader, _employees, period, _formula = await _setup(tmp_path)
        storage = FakeStorage()
        patches = _patch_world(factory, storage)
        try:
            for p in patches:
                p.start()
            async with await _client(factory, admin) as admin_client:
                content = await _template(admin_client, period.id)
                payload = _valid_workbook(content)
                body = await _enqueue(admin_client, payload, period.id)
            async with await _client(factory, uploader) as other_client:
                detail = await other_client.get(
                    f"/api/v1/kpi-uploads/monthly-data/jobs/{body['job_id']}"
                )
                assert detail.status_code == 404
                listing = await other_client.get("/api/v1/kpi-uploads/monthly-data/jobs")
                assert listing.status_code == 200
                assert listing.json() == {"jobs": [], "total": 0}
        finally:
            for p in patches:
                p.stop()
            limiter.enabled = True
            await engine.dispose()

    asyncio.run(scenario())


def test_super_admin_list_sees_all_jobs(tmp_path):
    """Case 8: the Super Admin list shows jobs owned by others."""

    async def scenario():
        engine, factory, admin, uploader, _employees, period, _formula = await _setup(tmp_path)
        storage = FakeStorage()
        patches = _patch_world(factory, storage)
        try:
            for p in patches:
                p.start()
            async with await _client(factory, admin) as admin_client:
                content = await _template(admin_client, period.id)
            async with await _client(factory, uploader) as uploader_client:
                payload = _valid_workbook(content)
                body = await _enqueue(uploader_client, payload, period.id)
            async with await _client(factory, admin) as admin_client:
                detail = await admin_client.get(
                    f"/api/v1/kpi-uploads/monthly-data/jobs/{body['job_id']}"
                )
                assert detail.status_code == 200
                assert detail.json()["status"] == "queued"
                listing = await admin_client.get("/api/v1/kpi-uploads/monthly-data/jobs")
                assert listing.status_code == 200
                assert listing.json()["total"] == 1
                assert listing.json()["jobs"][0]["job_id"] == body["job_id"]
        finally:
            for p in patches:
                p.stop()
            limiter.enabled = True
            await engine.dispose()

    asyncio.run(scenario())


def test_stuck_running_job_is_reclaimed(tmp_path):
    """Case 9: a RUNNING job with a stale heartbeat goes back to
    QUEUED (attempts left) or FAILED/timeout (attempts spent)."""

    async def scenario():
        engine, factory, admin, _uploader, _employees, period, _formula = await _setup(tmp_path)
        storage = FakeStorage()
        patches = _patch_world(factory, storage)
        try:
            for p in patches:
                p.start()
            now = utc_now_naive()
            stale = now - timedelta(minutes=20)
            async with factory() as db:
                first = KpiUploadJob(
                    owner_id=admin.id,
                    owner_name="QA Admin",
                    status=KpiUploadJobStatus.RUNNING.value,
                    source_object_key="kpi-upload-staging/stale-1.xlsx",
                    source_filename="stale-1.xlsx",
                    period_id=period.id,
                    period_label=period.label,
                    rows_total=2,
                    attempt_count=1,
                    started_at=stale,
                    last_heartbeat_at=stale,
                )
                spent = KpiUploadJob(
                    owner_id=admin.id,
                    owner_name="QA Admin",
                    status=KpiUploadJobStatus.RUNNING.value,
                    source_object_key="kpi-upload-staging/stale-2.xlsx",
                    source_filename="stale-2.xlsx",
                    period_id=period.id,
                    period_label=period.label,
                    rows_total=2,
                    attempt_count=3,
                    started_at=stale,
                    last_heartbeat_at=stale,
                )
                db.add_all([first, spent])
                await db.commit()
                first_id, spent_id = first.id, spent.id
            async with factory() as db:
                reclaimed = await _reclaim_stuck_jobs(db, now=now)
            assert reclaimed == 2
            first_after = await _job_row(factory, first_id)
            assert first_after.status == KpiUploadJobStatus.QUEUED.value
            assert first_after.started_at is None
            spent_after = await _job_row(factory, spent_id)
            assert spent_after.status == KpiUploadJobStatus.FAILED.value
            assert spent_after.error_code == "timeout"
            assert spent_after.finished_at is not None
        finally:
            for p in patches:
                p.stop()
            limiter.enabled = True
            await engine.dispose()

    asyncio.run(scenario())


def test_stuck_job_reclaimed_then_completed_by_pickup(tmp_path):
    """Case 9 (end to end): a stuck RUNNING job is reclaimed by the
    pickup and then runs to SUCCEEDED."""

    async def scenario():
        engine, factory, admin, _uploader, _employees, period, _formula = await _setup(tmp_path)
        storage = FakeStorage()
        patches = _patch_world(factory, storage)
        try:
            for p in patches:
                p.start()
            async with await _client(factory, admin) as client:
                content = await _template(client, period.id)
                payload = _valid_workbook(content)
                body = await _enqueue(client, payload, period.id)
                job_id = body["job_id"]
                stale = utc_now_naive() - timedelta(minutes=20)
                async with factory() as db:
                    job = await db.get(KpiUploadJob, UUID(job_id))
                    job.status = KpiUploadJobStatus.RUNNING.value
                    job.attempt_count = 1
                    job.started_at = stale
                    job.last_heartbeat_at = stale
                    await db.commit()
                processed = await run_kpi_upload_pickup()
                assert str(processed) == job_id
                detail = await client.get(
                    f"/api/v1/kpi-uploads/monthly-data/jobs/{job_id}"
                )
                assert detail.json()["status"] == "succeeded"
                assert detail.json()["rows_processed"] == 2
        finally:
            for p in patches:
                p.stop()
            limiter.enabled = True
            await engine.dispose()

    asyncio.run(scenario())


def test_two_jobs_run_serially_one_per_pickup(tmp_path):
    """Case 10: two queued jobs never overlap — each pickup runs
    exactly one, and the second stays QUEUED until its own tick."""

    async def scenario():
        engine, factory, admin, _uploader, _employees, period, _formula = await _setup(tmp_path)
        storage = FakeStorage()
        patches = _patch_world(factory, storage)
        try:
            for p in patches:
                p.start()
            async with await _client(factory, admin) as client:
                content = await _template(client, period.id)
                first = await _enqueue(client, _valid_workbook(content), period.id)
                second = await _enqueue(client, _valid_workbook(content), period.id)
                processed = await run_kpi_upload_pickup()
                assert str(processed) == first["job_id"]
                first_after = await _job_row(factory, first["job_id"])
                second_after = await _job_row(factory, second["job_id"])
                assert first_after.status == KpiUploadJobStatus.SUCCEEDED.value
                assert first_after.attempt_count == 1
                assert second_after.status == KpiUploadJobStatus.QUEUED.value
                assert second_after.attempt_count == 0
                processed = await run_kpi_upload_pickup()
                assert str(processed) == second["job_id"]
                second_after = await _job_row(factory, second["job_id"])
                assert second_after.status == KpiUploadJobStatus.SUCCEEDED.value
                assert second_after.attempt_count == 1
                assert await run_kpi_upload_pickup() is None
        finally:
            for p in patches:
                p.stop()
            limiter.enabled = True
            await engine.dispose()

    asyncio.run(scenario())


def test_prune_removes_old_finished_jobs_and_orphans(tmp_path):
    """Case 11: finished jobs older than 30 days are hard-deleted
    with their staged objects, staging orphans older than a day go
    too, and QUEUED jobs are never age-pruned."""

    async def scenario():
        engine, factory, admin, _uploader, _employees, period, _formula = await _setup(tmp_path)
        storage = FakeStorage()
        patches = _patch_world(factory, storage)
        try:
            for p in patches:
                p.start()
            now = utc_now_naive()
            old_finished_at = now - timedelta(days=31)
            storage.objects["kpi-upload-staging/old.xlsx"] = (
                b"old",
                datetime.now(UTC) - timedelta(days=31),
            )
            storage.objects["kpi-upload-staging/orphan-old.xlsx"] = (
                b"orphan",
                datetime.now(UTC) - timedelta(days=2),
            )
            storage.objects["kpi-upload-staging/orphan-fresh.xlsx"] = (
                b"orphan",
                datetime.now(UTC),
            )
            async with factory() as db:
                old = KpiUploadJob(
                    owner_id=admin.id,
                    owner_name="QA Admin",
                    status=KpiUploadJobStatus.SUCCEEDED.value,
                    source_object_key="kpi-upload-staging/old.xlsx",
                    source_filename="old.xlsx",
                    period_id=period.id,
                    period_label=period.label,
                    rows_total=2,
                    rows_processed=2,
                    finished_at=old_finished_at,
                )
                queued = KpiUploadJob(
                    owner_id=admin.id,
                    owner_name="QA Admin",
                    status=KpiUploadJobStatus.QUEUED.value,
                    source_object_key="kpi-upload-staging/queued.xlsx",
                    source_filename="queued.xlsx",
                    period_id=period.id,
                    period_label=period.label,
                    rows_total=2,
                )
                db.add_all([old, queued])
                await db.commit()
                old_id, queued_id = old.id, queued.id
            result = await prune_kpi_upload_jobs()
            assert result["pruned_jobs"] == 1
            assert result["deleted_objects"] == 1
            assert result["deleted_orphans"] == 1
            assert await _job_row(factory, old_id) is None
            assert await _job_row(factory, queued_id) is not None
            assert "kpi-upload-staging/old.xlsx" not in storage.objects
            assert "kpi-upload-staging/orphan-old.xlsx" not in storage.objects
            assert "kpi-upload-staging/orphan-fresh.xlsx" in storage.objects
        finally:
            for p in patches:
                p.stop()
            limiter.enabled = True
            await engine.dispose()

    asyncio.run(scenario())

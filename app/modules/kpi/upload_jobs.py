"""KPI upload background jobs (F6).

The request path (``upload_router``) parses + validates the workbook,
stages it to MinIO, inserts a QUEUED ``KpiUploadJob`` row and returns
202. This module owns everything after that: the scheduler pickup
claims one job at a time (serial — two jobs on the same period would
collide on the unique record constraint), the worker re-parses and
re-validates the staged file (never trusting the enqueue snapshots
for execution), runs the engine in ONE transaction, sends notices
only after that commit, and reports the outcome on a SEPARATE
session — so an engine rollback never erases the failure report.

Transaction boundaries (the trap):
- Tx-2 claim: atomic UPDATE ... WHERE status='QUEUED' (0 rows =
  someone else claimed it).
- Tx-3 engine: one session, one commit; any exception rolls back
  every record and every notice for that file.
- Tx-4 notices: ``flush_pending_notifications`` after Tx-3 commits.
- Tx-5 status: SUCCEEDED/FAILED on a separate session, own commit.

Progress + heartbeat updates also go through the status session so
polling sees them while the engine runs; they are best-effort (a
locked status write must never fail the engine run).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.timezone import utc_now_naive
from app.database.connection import AsyncSessionLocal
from app.modules.kpi.engine import KPICalculationEngine, _ensure_period_open
from app.modules.kpi.models import (
    KpiUploadJob,
    KpiUploadJobStatus,
    PeriodType,
)
from app.modules.kpi.overachievement import flush_pending_notifications
from app.modules.kpi.upload_router import (
    STAGING_FOLDER,
    UploadValidationError,
    _build_upload_rows,
    _department_names_for_formulas,
    _parse_upload_contents,
    _period_month_range,
    _resolve_period,
)
from app.modules.users.models import User
from app.services.storage_service import storage_service

logger = logging.getLogger(__name__)

STAGING_FOLDER = "kpi-upload-staging"
MAX_ATTEMPTS = 3
STUCK_AFTER_MINUTES = 15
RETENTION_DAYS = 30
PROGRESS_EVERY_ROWS = 10
STALE_STAGING_HOURS = 24
PRUNE_BATCH_SIZE = 1000

QUEUED = KpiUploadJobStatus.QUEUED.value
RUNNING = KpiUploadJobStatus.RUNNING.value
SUCCEEDED = KpiUploadJobStatus.SUCCEEDED.value
FAILED = KpiUploadJobStatus.FAILED.value


def _as_naive(value: datetime | None) -> datetime | None:
    """Compare stored timestamps as naive UTC (SQLite stores
    tz-aware values without their offset)."""
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


# ---------------------------------------------------------------------------
# Status session (Tx-5): the job row NEVER shares a session with the
# engine work, so an engine rollback cannot erase the report.
# ---------------------------------------------------------------------------


async def _set_job_fields(job_id: UUID, **fields) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(KpiUploadJob, job_id)
        if job is None:
            logger.warning("Upload job %s vanished before status update.", job_id)
            return
        for key, value in fields.items():
            setattr(job, key, value)
        await db.commit()


async def _set_job_progress(job_id: UUID, rows_processed: int) -> None:
    """Best-effort: a locked status write must never fail the run."""
    try:
        await _set_job_fields(
            job_id,
            rows_processed=rows_processed,
            last_heartbeat_at=utc_now_naive(),
        )
    except Exception:
        logger.warning(
            "Upload job %s: progress update failed (engine run continues).",
            job_id,
            exc_info=True,
        )


async def _fail_job(
    job_id: UUID, *, error_code: str, error_message: str, error_detail=None
) -> None:
    try:
        await _set_job_fields(
            job_id,
            status=FAILED,
            error_code=error_code,
            error_message=error_message,
            error_detail=error_detail,
            finished_at=utc_now_naive(),
        )
    except Exception:
        # The job stays RUNNING and the reclaim will retry it — loud
        # on purpose, so a stuck report is never silent.
        logger.exception(
            "Upload job %s: FAILED status could not be recorded.", job_id
        )


# ---------------------------------------------------------------------------
# Pickup (Tx-2): claim one job at a time, reclaim stuck ones.
# ---------------------------------------------------------------------------


async def _reclaim_stuck_jobs(db: AsyncSession, *, now: datetime) -> int:
    """RUNNING jobs whose heartbeat is older than 15 minutes go back
    to QUEUED (attempts left) or FAILED/timeout (attempts spent)."""
    running = (
        await db.execute(
            select(KpiUploadJob).where(KpiUploadJob.status == RUNNING)
        )
    ).scalars().all()
    reclaimed = 0
    for job in running:
        heartbeat = _as_naive(
            job.last_heartbeat_at or job.started_at or job.created_at
        )
        if heartbeat is None:
            continue
        if now - heartbeat <= timedelta(minutes=STUCK_AFTER_MINUTES):
            continue
        if job.attempt_count < MAX_ATTEMPTS:
            job.status = QUEUED
            job.started_at = None
            job.last_heartbeat_at = None
            logger.warning(
                "Upload job %s stuck; re-queued (attempt %s/%s).",
                job.id,
                job.attempt_count,
                MAX_ATTEMPTS,
            )
        else:
            job.status = FAILED
            job.error_code = "timeout"
            job.error_message = (
                "Processing did not finish in time. Please upload the file again."
            )
            job.finished_at = now
            logger.warning(
                "Upload job %s stuck past %s attempts; marked FAILED/timeout.",
                job.id,
                MAX_ATTEMPTS,
            )
        reclaimed += 1
    if reclaimed:
        await db.commit()
    return reclaimed


async def _claim_oldest_queued_job(db: AsyncSession, *, now: datetime) -> UUID | None:
    """Atomic claim: the UPDATE only touches a row that is still
    QUEUED, so two pickups can never claim the same job."""
    oldest_id = (
        await db.execute(
            select(KpiUploadJob.id)
            .where(KpiUploadJob.status == QUEUED)
            .order_by(KpiUploadJob.created_at, KpiUploadJob.id)
            .limit(1)
        )
    ).scalars().first()
    if oldest_id is None:
        return None
    result = await db.execute(
        update(KpiUploadJob)
        .where(
            KpiUploadJob.id == oldest_id,
            KpiUploadJob.status == QUEUED,
        )
        .values(
            status=RUNNING,
            started_at=now,
            last_heartbeat_at=now,
            attempt_count=KpiUploadJob.attempt_count + 1,
        )
    )
    await db.commit()
    if result.rowcount != 1:
        return None
    return oldest_id


# ---------------------------------------------------------------------------
# Worker (Tx-3 engine, Tx-4 notices, Tx-5 status).
# ---------------------------------------------------------------------------


async def _load_job_owner(db: AsyncSession, owner_id: UUID | None):
    """The owner, with everything the upload validation needs
    (role, position, department memberships)."""
    if owner_id is None:
        return None
    return (
        await db.execute(
            select(User)
            .where(User.id == owner_id)
            .options(
                selectinload(User.role),
                selectinload(User.position),
                selectinload(User.user_departments),
            )
        )
    ).scalars().first()


async def _run_upload_job(job_id: UUID) -> None:
    """Run one claimed job to completion. The job row is loaded on a
    fresh session; the engine runs on its own session (Tx-3); the
    outcome is written on a separate session (Tx-5)."""
    async with AsyncSessionLocal() as db:
        job = await db.get(KpiUploadJob, job_id)
        if job is None or job.status != RUNNING:
            return
        owner = await _load_job_owner(db, job.owner_id)
        period_id = job.period_id
        source_object_key = job.source_object_key
        source_filename = job.source_filename
        content_hash = job.content_hash

    if owner is None:
        await _fail_job(
            job_id,
            error_code="processing_error",
            error_message=(
                "The uploader's account is no longer available, so this "
                "upload cannot be processed."
            ),
        )
        return

    # Re-read the staged file; never trust the enqueue snapshot.
    try:
        contents = await storage_service.download_file(source_object_key)
    except Exception as exc:
        logger.warning("Upload job %s: staged file unreadable: %s", job_id, exc)
        await _fail_job(
            job_id,
            error_code="processing_error",
            error_message="The staged file could not be read. Please upload the file again.",
        )
        return
    if content_hash and hashlib.sha256(contents).hexdigest() != content_hash:
        await _fail_job(
            job_id,
            error_code="processing_error",
            error_message="The staged file changed before processing. Please upload the file again.",
        )
        return

    # Re-parse + re-validate the staged file on a fresh session.
    try:
        async with AsyncSessionLocal() as db:
            period = await _resolve_period(
                db, period_id=period_id, target_month=None
            )
            extension = (
                source_filename.rsplit(".", 1)[-1].lower()
                if "." in source_filename
                else ""
            )
            workbook = await _parse_upload_contents(contents, f".{extension}")
            valid_rows, errors = await _build_upload_rows(
                workbook, db, owner, period
            )
    except UploadValidationError as exc:
        await _fail_job(
            job_id,
            error_code="validation_error",
            error_message="The file no longer passes validation.",
            error_detail=list(exc.errors),
        )
        return
    if errors:
        await _fail_job(
            job_id,
            error_code="validation_error",
            error_message="The file no longer passes validation.",
            error_detail=list(errors),
        )
        return

    # Tx-3: the engine, one transaction, all or nothing.
    period_range = _period_month_range(period)
    try:
        async with AsyncSessionLocal() as db:
            await _ensure_period_open(db, period)
            department_names = await _department_names_for_formulas(
                db, {row.formula.department_id for row in valid_rows}
            )
            pending_notifications: list[dict] = []
            for index, row in enumerate(valid_rows):
                saved_record = await KPICalculationEngine.execute_kpi_calculation(
                    db=db,
                    user=row.user,
                    period_start=period_range.start_inclusive,
                    period_end=period_range.end_exclusive,
                    period_type=PeriodType.MONTHLY,
                    task_data=row.task_data,
                    commit=False,
                    pending_notifications=pending_notifications,
                    formula=row.formula,
                    period=period,
                    department_name=department_names.get(row.formula.department_id),
                    period_locked=True,
                )
                if saved_record is None or (
                    str(getattr(saved_record, "formula_id", ""))
                    != str(row.formula.id)
                ):
                    raise RuntimeError(
                        "KPI calculation did not produce the expected record."
                    )
                await db.flush()
                if (index + 1) % PROGRESS_EVERY_ROWS == 0:
                    await _set_job_progress(job_id, index + 1)
            await db.commit()
            # Tx-4: notices only after the engine transaction committed.
            await flush_pending_notifications(db, pending_notifications)
    except Exception as exc:
        logger.exception("Upload job %s: engine failed; all rows rolled back.", job_id)
        await _fail_job(
            job_id,
            error_code="processing_error",
            error_message="The server could not process this file. Please try again.",
        )
        return

    await _set_job_fields(
        job_id,
        status=SUCCEEDED,
        rows_processed=len(valid_rows),
        finished_at=utc_now_naive(),
    )


async def run_kpi_upload_pickup() -> UUID | None:
    """Scheduler entry (every 30s, advisory-locked by the caller):
    reclaim stuck jobs, then claim and run ONE job to completion."""
    now = utc_now_naive()
    async with AsyncSessionLocal() as db:
        await _reclaim_stuck_jobs(db, now=now)
        job_id = await _claim_oldest_queued_job(db, now=now)
    if job_id is None:
        return None
    logger.info("Upload job %s claimed; running.", job_id)
    await _run_upload_job(job_id)
    return job_id


# ---------------------------------------------------------------------------
# Daily prune: finished jobs older than 30 days, their staged objects,
# and staging orphans (Tx-1 left a file but no job row).
# ---------------------------------------------------------------------------


async def prune_kpi_upload_jobs() -> dict:
    now = utc_now_naive()
    cutoff = now - timedelta(days=RETENTION_DAYS)
    pruned_keys: list[str] = []
    async with AsyncSessionLocal() as db:
        finished = (
            await db.execute(
                select(KpiUploadJob)
                .where(
                    KpiUploadJob.status.in_([SUCCEEDED, FAILED]),
                    KpiUploadJob.finished_at < cutoff,
                )
                .limit(PRUNE_BATCH_SIZE)
            )
        ).scalars().all()
        for job in finished:
            pruned_keys.append(job.source_object_key)
            await db.delete(job)
        await db.commit()

    deleted_keys: list[str] = []
    for key in pruned_keys:
        try:
            await storage_service.delete_file(key)
            deleted_keys.append(key)
        except Exception:
            logger.warning("Upload prune: staged object %s could not be deleted.", key)

    # Tx-1 orphans: staged objects older than a day with no job row.
    orphan_keys: list[str] = []
    try:
        staged = storage_service.list_object_keys(f"{STAGING_FOLDER}/")
    except Exception:
        logger.warning("Upload prune: staging list failed; orphans kept.", exc_info=True)
        staged = []
    if staged:
        staged_keys = [key for key, _ in staged]
        async with AsyncSessionLocal() as db:
            known = set(
                (
                    await db.execute(
                        select(KpiUploadJob.source_object_key).where(
                            KpiUploadJob.source_object_key.in_(staged_keys)
                        )
                    )
                ).scalars().all()
            )
        orphan_cutoff = now - timedelta(hours=STALE_STAGING_HOURS)
        for key, last_modified in staged:
            if key in known:
                continue
            modified = _as_naive(last_modified)
            if modified is not None and modified >= orphan_cutoff:
                continue
            orphan_keys.append(key)
        for key in orphan_keys:
            try:
                await storage_service.delete_file(key)
            except Exception:
                logger.warning("Upload prune: orphan %s could not be deleted.", key)

    logger.info(
        "Upload prune: %s finished jobs removed, %s staged objects deleted, "
        "%s orphans deleted.",
        len(pruned_keys),
        len(deleted_keys),
        len(orphan_keys),
    )
    return {
        "pruned_jobs": len(pruned_keys),
        "deleted_objects": len(deleted_keys),
        "deleted_orphans": len(orphan_keys),
    }

"""Departed-employee retention: downloads, grants, locks, identities.

Implements the Stage 2 rules on top of the Stage 1 schema:
- Report downloads need the Super Admin role OR an active row in
  report_download_grants covering the requested scope. The check
  deliberately does NOT consult the permission catalog: ROLE_DEFAULTS
  auto-grants Admins nearly every catalog permission, which would
  silently break the "Super Admin + explicitly granted users only"
  rule.
- The hard-delete year lock needs BOTH a backup stamp in
  yearly_retention AND a company-wide YEARLY download whose window
  covers the whole calendar year. Department-scope rows, partial
  windows, and CUSTOM full-range rows never count.
- Hard delete captures a departed_identities registry row (when the
  person has KPI records or tasks) and links the kept rows to it, so
  grouping and a later Relink key on the identity id — never on
  name/email text (names repeat; emails get re-registered).
"""

import calendar as _calendar
import re
from datetime import date, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.departed_identity import DepartedIdentity
from app.models.report_download import (
    ReportDownload,
    ReportDownloadGrant,
    ReportGrantScope,
    ReportType,
)
from app.models.yearly_retention import YearlyRetention
from app.modules.departments.models import Department
from app.modules.kpi.models import KPIRecord
from app.modules.tasks.models.task import Task
from app.modules.users.models import User

SUPER_ADMIN_ROLE = "Super Admin"
# How long the snapshot-level task rows of a departed identity are
# kept after the hard delete (Zenn, 2026-10-23): one year for
# handover, then the scheduler purges them. KPI records/history are
# kept forever and are never part of any purge.
TASK_SNAPSHOT_RETENTION_DAYS = 365


def is_super_admin(user: User) -> bool:
    return getattr(getattr(user, "role", None), "name", None) == SUPER_ADMIN_ROLE


def write_audit(
    db: AsyncSession,
    *,
    actor_id: UUID | None,
    action: str,
    table_name: str,
    record_id: str | None = None,
    new_value: dict | None = None,
) -> None:
    """Stage an AuditLog row in the caller's transaction."""
    db.add(
        AuditLog(
            user_id=actor_id,
            action=action,
            table_name=table_name,
            record_id=record_id,
            new_value=new_value,
        )
    )


# ---------------------------------------------------------------------------
# Download authorization (grants, never the permission catalog)
# ---------------------------------------------------------------------------


async def active_grants(db: AsyncSession, user_id: UUID) -> list[ReportDownloadGrant]:
    rows = (
        await db.execute(
            select(ReportDownloadGrant).where(
                ReportDownloadGrant.user_id == user_id,
                ReportDownloadGrant.is_active.is_(True),
                ReportDownloadGrant.is_deleted.is_(False),
            )
        )
    ).scalars().all()
    return list(rows)


async def ensure_download_allowed(
    db: AsyncSession,
    user: User,
    *,
    company_wide: bool,
    department_id: UUID | None,
) -> None:
    """403 unless Super Admin or an active grant covers the scope.

    The grant is checked exactly as recorded: a DEPARTMENT grant
    covers that department only (never inferred from the user's
    current department), and only a COMPANY grant covers a
    company-wide download.
    """
    if is_super_admin(user):
        return
    grants = await active_grants(db, user.id)
    for grant in grants:
        if grant.scope_type == ReportGrantScope.COMPANY.value:
            return
        if (
            not company_wide
            and grant.scope_type == ReportGrantScope.DEPARTMENT.value
            and grant.department_id == department_id
        ):
            return
    if company_wide:
        raise HTTPException(
            status_code=403,
            detail="Company-wide report downloads need a company-wide download grant from a Super Admin.",
        )
    raise HTTPException(
        status_code=403,
        detail="Report downloads for this department need a download grant from a Super Admin.",
    )


# ---------------------------------------------------------------------------
# Download windows
# ---------------------------------------------------------------------------


def resolve_window(
    report_type: ReportType,
    *,
    year: int | None,
    month: int | None,
    quarter: int | None,
    start: date | None,
    end: date | None,
) -> tuple[date, date]:
    """Inclusive [start, end] dates for the requested window kind."""
    if report_type == ReportType.MONTHLY:
        if year is None or month is None or not 1 <= month <= 12:
            raise HTTPException(status_code=422, detail="A monthly download needs a valid year and month.")
        last_day = _calendar.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last_day)
    if report_type == ReportType.QUARTERLY:
        if year is None or quarter is None or not 1 <= quarter <= 4:
            raise HTTPException(status_code=422, detail="A quarterly download needs a valid year and quarter (1-4).")
        first_month = 3 * (quarter - 1) + 1
        last_month = first_month + 2
        last_day = _calendar.monthrange(year, last_month)[1]
        return date(year, first_month, 1), date(year, last_month, last_day)
    if report_type == ReportType.YEARLY:
        if year is None:
            raise HTTPException(status_code=422, detail="A yearly download needs a year.")
        return date(year, 1, 1), date(year, 12, 31)
    # CUSTOM
    if start is None or end is None:
        raise HTTPException(status_code=422, detail="A custom download needs both a start and an end date.")
    if end < start:
        raise HTTPException(status_code=422, detail="The end date must not be before the start date.")
    return start, end


def window_label(
    report_type: ReportType, window_start: date, window_end: date
) -> str:
    if report_type == ReportType.MONTHLY:
        return f"{window_start.year}-{window_start.month:02d}"
    if report_type == ReportType.QUARTERLY:
        return f"{window_start.year}-Q{(window_start.month - 1) // 3 + 1}"
    if report_type == ReportType.YEARLY:
        return str(window_start.year)
    return f"{window_start.isoformat()}_to_{window_end.isoformat()}"


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name.strip()).strip("-")
    return slug or "department"


def download_filename(
    report_type: ReportType,
    window_start: date,
    window_end: date,
    *,
    company_wide: bool,
    department_name: str | None,
) -> str:
    label = window_label(report_type, window_start, window_end)
    if company_wide:
        return f"kpi-report_company_{label}.csv"
    return f"kpi-report_dept-{_slug(department_name or 'department')}_{label}.csv"


# ---------------------------------------------------------------------------
# Year lock (hard-delete gate)
# ---------------------------------------------------------------------------


async def full_year_download_exists(db: AsyncSession, year: int) -> bool:
    """ONLY a company-wide YEARLY row covering Jan 1 - Dec 31 counts."""
    count = (
        await db.execute(
            select(func.count(ReportDownload.id)).where(
                ReportDownload.report_type == ReportType.YEARLY.value,
                ReportDownload.is_company_wide.is_(True),
                ReportDownload.period_start <= date(year, 1, 1),
                ReportDownload.period_end >= date(year, 12, 31),
                ReportDownload.is_deleted.is_(False),
            )
        )
    ).scalar_one()
    return count > 0


async def backup_stamp(db: AsyncSession, year: int) -> YearlyRetention | None:
    return (
        await db.execute(
            select(YearlyRetention).where(YearlyRetention.year == year)
        )
    ).scalars().first()


async def year_lock_status(db: AsyncSession, year: int) -> dict:
    stamp = await backup_stamp(db, year)
    backup_done = bool(stamp and stamp.backup_confirmed_at)
    downloaded = await full_year_download_exists(db, year)
    from app.core.timezone import APPLICATION_TIMEZONE

    year_ended = datetime.now(APPLICATION_TIMEZONE).date() > date(year, 12, 31)
    return {
        "year": year,
        "year_ended": year_ended,
        "backup_confirmed": backup_done,
        "backup_confirmed_at": stamp.backup_confirmed_at if stamp else None,
        "full_year_download_exists": downloaded,
        "unlocked": year_ended and backup_done and downloaded,
    }


async def ensure_hard_delete_unlocked(
    db: AsyncSession, *, user: User, today: date
) -> None:
    """Gate for deleting a DEPARTED account; raises 400 with the reason."""
    resigned = user.resigned_at
    if resigned is None:
        raise HTTPException(
            status_code=400,
            detail="This account has no leaving date recorded. Offboard the employee first (with the correct leaving date) before permanent deletion.",
        )
    if today.year <= resigned.year:
        raise HTTPException(
            status_code=400,
            detail=f"Permanent deletion is locked until the year {resigned.year} has ended.",
        )
    status = await year_lock_status(db, resigned.year)
    if not status["full_year_download_exists"]:
        raise HTTPException(
            status_code=400,
            detail=f"Permanent deletion is locked: the company-wide full-year report for {resigned.year} has not been downloaded yet.",
        )
    if not status["backup_confirmed"]:
        raise HTTPException(
            status_code=400,
            detail=f"Permanent deletion is locked: the backup for {resigned.year} has not been confirmed yet.",
        )


# ---------------------------------------------------------------------------
# Departed identity capture + relink
# ---------------------------------------------------------------------------


def display_name(user: User) -> str:
    return f"{user.first_name or ''} {user.last_name or ''}".strip()


async def stamp_resigned_snapshots(
    db: AsyncSession, user: User
) -> None:
    """Fill name + resigned-date snapshots on the user's kept rows.

    Runs at offboarding (and again as a backstop at hard delete):
    records were calculated before the person left, so the resigned
    date can only be stamped once it is known.
    """
    if user.resigned_at is None:
        return
    name = display_name(user)
    await db.execute(
        update(KPIRecord)
        .where(KPIRecord.user_id == user.id)
        .values(
            employee_name=func.coalesce(KPIRecord.employee_name, name),
            employee_resigned_at=user.resigned_at,
        )
    )
    await db.execute(
        update(Task)
        .where(Task.owner_id == user.id)
        .values(
            owner_name=func.coalesce(Task.owner_name, name),
            owner_resigned_at=user.resigned_at,
        )
    )


async def capture_departed_identity(
    db: AsyncSession, user: User
) -> DepartedIdentity | None:
    """Registry row + links for the kept rows; None if nothing is kept."""
    record_count = (
        await db.execute(
            select(func.count(KPIRecord.id)).where(KPIRecord.user_id == user.id)
        )
    ).scalar_one()
    task_count = (
        await db.execute(
            select(func.count(Task.id)).where(Task.owner_id == user.id)
        )
    ).scalar_one()
    if record_count == 0 and task_count == 0:
        return None
    # Department names via the membership table (read-only aid text).
    from app.modules.departments.models import UserDepartment

    dept_rows = (
        await db.execute(
            select(Department.name)
            .join(UserDepartment, UserDepartment.department_id == Department.id)
            .where(UserDepartment.user_id == user.id)
        )
    ).scalars().all()
    # The stored email may carry the soft-delete rename prefix; the
    # identity keeps the person's real email for matching.
    email = user.email
    if email.startswith("deleted_"):
        parts = email.split("_", 2)
        if len(parts) == 3:
            email = parts[2]
    existing = (
        await db.execute(
            select(DepartedIdentity).where(
                DepartedIdentity.original_user_id == user.id
            )
        )
    ).scalars().first()
    identity = existing or DepartedIdentity(
        original_user_id=user.id,
        employee_name=display_name(user),
        email=email,
        department_name=", ".join(dept_rows) or None,
        resigned_at=user.resigned_at,
    )
    if existing is None:
        db.add(identity)
    await db.flush()
    await db.execute(
        update(KPIRecord)
        .where(KPIRecord.user_id == user.id)
        .values(departed_identity_id=identity.id)
    )
    await db.execute(
        update(Task)
        .where(Task.owner_id == user.id)
        .values(departed_identity_id=identity.id)
    )
    return identity


async def stamp_task_outcomes(db: AsyncSession, task_ids: list) -> None:
    """Copy each completed task's outcome facts onto the task row.

    Runs at hard-delete time, BEFORE the history/review trail is
    purged: the completion moment comes from the history entry whose
    new_value is COMPLETED (fallback: the task's own updated_at),
    and the result text from the latest review on the task
    (fallback: that history entry's remarks). Non-completed tasks
    keep just their row (title/status/due date) as the snapshot.
    """
    if not task_ids:
        return
    from app.modules.tasks.models.task import TaskStatus
    from app.modules.tasks.models.task_history import TaskHistory
    from app.modules.tasks.models.task_review import TaskReview

    tasks = (
        await db.execute(select(Task).where(Task.id.in_(task_ids)))
    ).scalars().all()
    for task in tasks:
        if task.status != TaskStatus.COMPLETED or task.outcome_snapshot:
            continue
        completion_entry = (
            await db.execute(
                select(TaskHistory)
                .where(
                    TaskHistory.task_id == task.id,
                    TaskHistory.new_value == TaskStatus.COMPLETED.value,
                )
                .order_by(TaskHistory.created_at.desc())
            )
        ).scalars().first()
        completed_at = (
            completion_entry.created_at if completion_entry else task.updated_at
        )
        latest_review = (
            await db.execute(
                select(TaskReview)
                .where(TaskReview.task_id == task.id)
                .order_by(TaskReview.created_at.desc())
            )
        ).scalars().first()
        result = None
        if latest_review is not None:
            result = latest_review.action
            if latest_review.comments:
                result = f"{latest_review.action} — {latest_review.comments}"
        elif completion_entry is not None and completion_entry.remarks:
            result = completion_entry.remarks
        task.outcome_snapshot = {
            "completed_at": completed_at.isoformat() if completed_at else None,
            "result": result,
        }
        db.add(task)


async def purge_expired_task_snapshots(
    db: AsyncSession, *, today: date
) -> list[dict]:
    """Delete snapshot-level task rows whose retention year is over.

    The clock starts at the hard delete — read from the surviving
    USER_HARD_DELETED audit row for the identity's original user
    (audit rows are kept, so the timestamp always survives). Only
    tasks linked to the departed identity are touched; KPI records
    and history are NEVER purged. Each identity actually purged
    leaves a TASK_SNAPSHOTS_PURGED audit row. Returns one summary
    dict per purged identity.
    """
    from datetime import timedelta

    from sqlalchemy import delete as sa_delete

    from app.models.audit_log import AuditLog
    from app.modules.tasks.models.task_evidence import TaskEvidence
    from app.modules.tasks.models.task_history import TaskHistory
    from app.modules.tasks.models.task_review import TaskReview

    purged: list[dict] = []
    identities = (
        await db.execute(
            select(DepartedIdentity).where(DepartedIdentity.is_deleted.is_(False))
        )
    ).scalars().all()
    for identity in identities:
        hard_delete_row = (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.action == "USER_HARD_DELETED",
                    AuditLog.record_id == str(identity.original_user_id),
                )
                .order_by(AuditLog.created_at)
            )
        ).scalars().first()
        if hard_delete_row is None:
            continue  # account not hard-deleted yet — live soft state
        hard_deleted_on = hard_delete_row.created_at.date()
        if hard_deleted_on + timedelta(days=TASK_SNAPSHOT_RETENTION_DAYS) > today:
            continue
        task_ids = list(
            (
                await db.execute(
                    select(Task.id).where(
                        Task.departed_identity_id == identity.id
                    )
                )
            ).scalars().all()
        )
        if not task_ids:
            continue
        # Stragglers first (normally already purged at hard delete),
        # then the snapshot rows themselves.
        for model in (TaskEvidence, TaskHistory, TaskReview):
            await db.execute(
                sa_delete(model).where(model.task_id.in_(task_ids))
            )
        await db.execute(sa_delete(Task).where(Task.id.in_(task_ids)))
        write_audit(
            db,
            actor_id=None,
            action="TASK_SNAPSHOTS_PURGED",
            table_name="tasks",
            record_id=str(identity.id),
            new_value={
                "employee": identity.employee_name,
                "hard_deleted_at": hard_deleted_on.isoformat(),
                "tasks_removed": len(task_ids),
            },
        )
        purged.append(
            {
                "identity_id": identity.id,
                "employee": identity.employee_name,
                "tasks_removed": len(task_ids),
            }
        )
    await db.commit()
    return purged


async def identity_counts(
    db: AsyncSession, identity_id: UUID
) -> tuple[int, int]:
    records = (
        await db.execute(
            select(func.count(KPIRecord.id)).where(
                KPIRecord.departed_identity_id == identity_id
            )
        )
    ).scalar_one()
    from app.modules.tasks.models.task import TaskStatus

    tasks = (
        await db.execute(
            select(func.count(Task.id)).where(
                Task.departed_identity_id == identity_id,
                Task.status == TaskStatus.COMPLETED,
            )
        )
    ).scalar_one()
    return int(records), int(tasks)

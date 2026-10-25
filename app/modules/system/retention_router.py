"""Retention HTTP surface: report downloads, download grants,
year-lock status/backup confirmation, departed identities + Relink.

All grant/lock/identity management is Super-Admin-only at the API
level (role check, not a catalog permission — see retention_service).
Downloads themselves are allowed for Super Admins and for users with
an active grant covering the requested scope.
"""

import csv
import io
from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.timezone import utc_now_naive
from app.database.connection import get_db
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
from app.modules.system import retention_service as rs
from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.users.models import User

router = APIRouter(prefix="/system", tags=["Retention"])


def _require_super_admin(current_user: User) -> None:
    if not rs.is_super_admin(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only Super Admins can manage report downloads and retention.",
        )


# ---------------------------------------------------------------------------
# Report download (window x scope)
# ---------------------------------------------------------------------------


@router.get("/report-download")
async def download_report(
    report_type: ReportType = Query(...),
    year: int | None = Query(None),
    month: int | None = Query(None),
    quarter: int | None = Query(None),
    start: date | None = Query(None),
    end: date | None = Query(None),
    scope: str = Query("company", pattern="^(company|department)$"),
    department_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download KPI records for one window and one scope as CSV.

    Window rule (also stated in the UI copy): a record is included
    when its period start date falls inside the selected window —
    a window crossing a period boundary includes every period that
    starts inside it. Departed employees' records are included,
    labelled "Resigned · <date>". Every download is recorded in
    report_downloads and in the audit log.
    """
    window_start, window_end = rs.resolve_window(
        report_type, year=year, month=month, quarter=quarter, start=start, end=end
    )
    company_wide = scope == "company"
    department = None
    if not company_wide:
        if department_id is None:
            raise HTTPException(status_code=422, detail="A department download needs department_id.")
        department = (
            await db.execute(select(Department).where(Department.id == department_id))
        ).scalars().first()
        if department is None:
            raise HTTPException(status_code=404, detail="Department not found.")
    await rs.ensure_download_allowed(
        db, current_user, company_wide=company_wide, department_id=department_id
    )

    stmt = (
        select(KPIRecord, User, Department, DepartedIdentity)
        .outerjoin(User, KPIRecord.user_id == User.id)
        .outerjoin(Department, KPIRecord.department_id == Department.id)
        .outerjoin(
            DepartedIdentity,
            KPIRecord.departed_identity_id == DepartedIdentity.id,
        )
        .where(
            KPIRecord.is_deleted.is_(False),
            func.date(KPIRecord.period_start) >= window_start,
            func.date(KPIRecord.period_start) <= window_end,
        )
    )
    if not company_wide:
        stmt = stmt.where(KPIRecord.department_id == department_id)
    stmt = stmt.order_by(KPIRecord.period_start, KPIRecord.id)
    rows = (await db.execute(stmt)).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Employee", "Email", "Department", "Period Start", "Period End",
         "Score", "Percentage", "Grade", "Status"]
    )
    dept_scores: dict[str, list[float]] = {}
    all_scores: list[float] = []
    for record, user, dept, identity in rows:
        name = (
            rs.display_name(user)
            if user is not None
            else (record.employee_name or (identity.employee_name if identity else "Unknown"))
        )
        email = user.email if user is not None else (identity.email if identity else "")
        dept_name = (
            dept.name if dept is not None else (record.department_name or "N/A")
        )
        label = (
            f"Resigned · {record.employee_resigned_at.isoformat()}"
            if record.employee_resigned_at
            else ""
        )
        writer.writerow(
            [
                name,
                email,
                dept_name,
                record.period_start.date().isoformat(),
                record.period_end.date().isoformat(),
                f"{record.weighted_score:.2f}",
                f"{record.percentage:.2f}",
                record.status_bucket.value
                if hasattr(record.status_bucket, "value")
                else str(record.status_bucket),
                label,
            ]
        )
        dept_scores.setdefault(dept_name, []).append(float(record.weighted_score))
        all_scores.append(float(record.weighted_score))
    for dept_name in sorted(dept_scores):
        scores = dept_scores[dept_name]
        writer.writerow(
            [f"Department average — {dept_name}", "", "", "", "",
             f"{sum(scores) / len(scores):.2f}", "", f"{len(scores)} records", ""]
        )
    if all_scores:
        writer.writerow(
            ["Company average", "", "", "", "",
             f"{sum(all_scores) / len(all_scores):.2f}", "",
             f"{len(all_scores)} records", ""]
        )

    filename = rs.download_filename(
        report_type,
        window_start,
        window_end,
        company_wide=company_wide,
        department_name=department.name if department else None,
    )
    db.add(
        ReportDownload(
            report_type=report_type.value,
            period_start=window_start,
            period_end=window_end,
            is_company_wide=company_wide,
            department_id=department_id if not company_wide else None,
            department_name=department.name if department else None,
            downloaded_by=current_user.id,
        )
    )
    rs.write_audit(
        db,
        actor_id=current_user.id,
        action="REPORT_DOWNLOAD",
        table_name="report_downloads",
        new_value={
            "filename": filename,
            "report_type": report_type.value,
            "window": [window_start.isoformat(), window_end.isoformat()],
            "scope": "company" if company_wide else f"department:{department_id}",
            "record_count": len(rows),
        },
    )
    await db.commit()
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Download grants (Super Admin only)
# ---------------------------------------------------------------------------


class GrantCreate(BaseModel):
    user_id: UUID
    scope_type: ReportGrantScope
    department_id: UUID | None = None


@router.get("/report-downloads/grants")
async def list_grants(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin(current_user)
    rows = (
        await db.execute(
            select(ReportDownloadGrant, User, Department)
            .join(User, ReportDownloadGrant.user_id == User.id)
            .outerjoin(
                Department, ReportDownloadGrant.department_id == Department.id
            )
            .where(ReportDownloadGrant.is_deleted.is_(False))
            .order_by(ReportDownloadGrant.created_at.desc())
        )
    ).all()
    return [
        {
            "id": str(grant.id),
            "user_id": str(grant.user_id),
            "user_name": rs.display_name(user),
            "user_email": user.email,
            "scope_type": grant.scope_type,
            "department_id": str(grant.department_id) if grant.department_id else None,
            "department_name": dept.name if dept else None,
            "is_active": grant.is_active,
            "granted_at": grant.created_at,
            "revoked_at": grant.revoked_at,
        }
        for grant, user, dept in rows
    ]


@router.post("/report-downloads/grants", status_code=201)
async def create_grant(
    payload: GrantCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin(current_user)
    if payload.scope_type == ReportGrantScope.DEPARTMENT and payload.department_id is None:
        raise HTTPException(status_code=422, detail="A department grant needs department_id.")
    if payload.scope_type == ReportGrantScope.COMPANY and payload.department_id is not None:
        raise HTTPException(status_code=422, detail="A company-wide grant must not name a department.")
    target = (
        await db.execute(select(User).where(User.id == payload.user_id))
    ).scalars().first()
    if target is None or target.is_deleted:
        raise HTTPException(status_code=404, detail="User not found.")
    if payload.department_id is not None:
        dept = (
            await db.execute(select(Department).where(Department.id == payload.department_id))
        ).scalars().first()
        if dept is None:
            raise HTTPException(status_code=404, detail="Department not found.")
    duplicate = (
        await db.execute(
            select(ReportDownloadGrant).where(
                ReportDownloadGrant.user_id == payload.user_id,
                ReportDownloadGrant.scope_type == payload.scope_type.value,
                ReportDownloadGrant.department_id.is_(payload.department_id)
                if payload.department_id is None
                else ReportDownloadGrant.department_id == payload.department_id,
                ReportDownloadGrant.is_active.is_(True),
                ReportDownloadGrant.is_deleted.is_(False),
            )
        )
    ).scalars().first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="This user already has an active grant for that scope.")
    grant = ReportDownloadGrant(
        user_id=payload.user_id,
        scope_type=payload.scope_type.value,
        department_id=payload.department_id,
        granted_by=current_user.id,
        is_active=True,
    )
    db.add(grant)
    await db.flush()
    rs.write_audit(
        db,
        actor_id=current_user.id,
        action="REPORT_GRANT_CREATED",
        table_name="report_download_grants",
        record_id=str(grant.id),
        new_value={
            "user_id": str(payload.user_id),
            "scope_type": payload.scope_type.value,
            "department_id": str(payload.department_id) if payload.department_id else None,
        },
    )
    await db.commit()
    return {"id": str(grant.id), "message": "Download grant created."}


@router.post("/report-downloads/grants/{grant_id}/revoke")
async def revoke_grant(
    grant_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin(current_user)
    grant = (
        await db.execute(
            select(ReportDownloadGrant).where(ReportDownloadGrant.id == grant_id)
        )
    ).scalars().first()
    if grant is None or grant.is_deleted:
        raise HTTPException(status_code=404, detail="Grant not found.")
    if grant.is_active:
        grant.is_active = False
        grant.revoked_at = utc_now_naive()
        grant.revoked_by = current_user.id
        rs.write_audit(
            db,
            actor_id=current_user.id,
            action="REPORT_GRANT_REVOKED",
            table_name="report_download_grants",
            record_id=str(grant.id),
            new_value={"user_id": str(grant.user_id), "scope_type": grant.scope_type},
        )
        await db.commit()
    return {"message": "Download grant revoked."}


# ---------------------------------------------------------------------------
# Year lock: status + backup confirmation (Super Admin only)
# ---------------------------------------------------------------------------


@router.get("/retention/lock-status")
async def lock_status(
    year: int = Query(..., ge=1900, le=9999),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin(current_user)
    return await rs.year_lock_status(db, year)


class BackupConfirm(BaseModel):
    year: int


@router.post("/retention/backup-confirm")
async def backup_confirm(
    payload: BackupConfirm,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin(current_user)
    stamp = await rs.backup_stamp(db, payload.year)
    if stamp is None:
        stamp = YearlyRetention(year=payload.year)
        db.add(stamp)
    stamp.backup_confirmed_at = utc_now_naive()
    stamp.backup_confirmed_by = current_user.id
    rs.write_audit(
        db,
        actor_id=current_user.id,
        action="RETENTION_BACKUP_CONFIRMED",
        table_name="yearly_retention",
        new_value={"year": payload.year},
    )
    await db.commit()
    return await rs.year_lock_status(db, payload.year)


# ---------------------------------------------------------------------------
# Departed identities + Relink (Super Admin only)
# ---------------------------------------------------------------------------


async def _identity_payload(db: AsyncSession, identity: DepartedIdentity) -> dict:
    records, completed_tasks = await rs.identity_counts(db, identity.id)
    return {
        "id": str(identity.id),
        "original_user_id": str(identity.original_user_id),
        "employee_name": identity.employee_name,
        "email": identity.email,
        "department_name": identity.department_name,
        "resigned_at": identity.resigned_at,
        "kpi_records": records,
        "completed_tasks": completed_tasks,
    }


@router.get("/departed-identities")
async def list_departed_identities(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_super_admin(current_user)
    identities = (
        await db.execute(
            select(DepartedIdentity)
            .where(DepartedIdentity.is_deleted.is_(False))
            .order_by(DepartedIdentity.created_at.desc())
        )
    ).scalars().all()
    return [await _identity_payload(db, identity) for identity in identities]


@router.get("/departed-identities/{identity_id}")
async def get_departed_identity(
    identity_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Relink preview step: the exact evidence + counts to confirm."""
    _require_super_admin(current_user)
    identity = (
        await db.execute(
            select(DepartedIdentity).where(DepartedIdentity.id == identity_id)
        )
    ).scalars().first()
    if identity is None or identity.is_deleted:
        raise HTTPException(status_code=404, detail="Departed identity not found.")
    return await _identity_payload(db, identity)


@router.get("/departed-identities/{identity_id}/tasks")
async def get_departed_identity_tasks(
    identity_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Handover view: what this departed person finished.

    The kept tasks at snapshot level — what the work was, its final
    status, and (for completed tasks) the outcome facts stamped
    onto the row before the activity trail was purged: completion
    date + result. No day-by-day activity survives here by design.
    """
    _require_super_admin(current_user)
    identity = (
        await db.execute(
            select(DepartedIdentity).where(DepartedIdentity.id == identity_id)
        )
    ).scalars().first()
    if identity is None or identity.is_deleted:
        raise HTTPException(status_code=404, detail="Departed identity not found.")
    tasks = (
        await db.execute(
            select(Task)
            .where(Task.departed_identity_id == identity.id)
            .order_by(Task.created_at)
        )
    ).scalars().all()
    payload = []
    for task in tasks:
        outcome = task.outcome_snapshot or {}
        payload.append(
            {
                "id": str(task.id),
                "title": task.title,
                "status": task.status.value
                if hasattr(task.status, "value")
                else str(task.status),
                "due_date": task.due_date,
                "completed_at": outcome.get("completed_at"),
                "result": outcome.get("result"),
                "owner_name": task.owner_name or identity.employee_name,
            }
        )
    return payload


class RelinkRequest(BaseModel):
    new_user_id: UUID


@router.post("/departed-identities/{identity_id}/relink")
async def relink_identity(
    identity_id: UUID,
    payload: RelinkRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Confirm step: re-attach the identity's kept records and
    completed tasks to a NEW account. Never automatic. Afterwards the
    records belong to the new account (no Resigned label); the story
    stays in the audit log."""
    _require_super_admin(current_user)
    identity = (
        await db.execute(
            select(DepartedIdentity).where(DepartedIdentity.id == identity_id)
        )
    ).scalars().first()
    if identity is None or identity.is_deleted:
        raise HTTPException(status_code=404, detail="Departed identity not found.")
    new_user = (
        await db.execute(select(User).where(User.id == payload.new_user_id))
    ).scalars().first()
    if new_user is None or new_user.is_deleted or not new_user.is_active:
        raise HTTPException(
            status_code=400,
            detail="The new account must be an active account. Create or restore the account first.",
        )
    if new_user.id == identity.original_user_id:
        raise HTTPException(
            status_code=400,
            detail="That is the original account id — if the account exists again, use Restore instead of Relink.",
        )
    records_before, tasks_before = await rs.identity_counts(db, identity.id)
    await db.execute(
        update(KPIRecord)
        .where(KPIRecord.departed_identity_id == identity.id)
        .values(
            user_id=new_user.id,
            departed_identity_id=None,
            employee_resigned_at=None,
        )
    )
    await db.execute(
        update(Task)
        .where(
            Task.departed_identity_id == identity.id,
            Task.status == TaskStatus.COMPLETED,
        )
        .values(
            owner_id=new_user.id,
            departed_identity_id=None,
            owner_resigned_at=None,
        )
    )
    remaining_records, remaining_tasks = await rs.identity_counts(db, identity.id)
    if remaining_records == 0:
        # Nothing left to group under this identity (any non-completed
        # tasks keep the row alive only while they reference it).
        still_linked = (
            await db.execute(
                select(func.count(Task.id)).where(
                    Task.departed_identity_id == identity.id
                )
            )
        ).scalar_one()
        if still_linked == 0:
            await db.delete(identity)
    rs.write_audit(
        db,
        actor_id=current_user.id,
        action="IDENTITY_RELINKED",
        table_name="departed_identities",
        record_id=str(identity.id),
        new_value={
            "employee_name": identity.employee_name,
            "email": identity.email,
            "new_user_id": str(new_user.id),
            "kpi_records_relinked": records_before,
            "tasks_relinked": tasks_before,
        },
    )
    await db.commit()
    return {
        "message": "Departed records relinked to the new account.",
        "new_user_id": str(new_user.id),
        "kpi_records_relinked": records_before,
        "tasks_relinked": tasks_before,
    }

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.timezone import APPLICATION_TIMEZONE  # 🌟 Added for Timezone Conversion
from app.database.connection import get_db
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.dashboard_schemas import DashboardSummaryResponse
from app.modules.kpi.dashboard_service import build_dashboard_summary
from app.modules.kpi.executive_intelligence_schemas import (
    ExecutiveEmployeeDetail,
    ExecutiveEmployeePage,
    ExecutiveOverview,
)
from app.modules.kpi.executive_intelligence_service import (
    build_executive_overview,
    get_executive_employee_detail,
    list_executive_employee_results,
)
from app.modules.kpi.models import KPIRecord, KPIStatus, PeriodType
from app.modules.notifications.models import Notification
from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.users.models import Position, User

# 🌟 လုံခြုံရေးအတွက် Role Checker ကို Import လုပ်ခြင်း
from app.modules.users.router import admin_checker

router = APIRouter()
superadmin_router = APIRouter()
admin_router = APIRouter()
unified_router = APIRouter()
SUPER_ADMIN_ROLE_MARKER = "SUPER ADMIN"


def _normalize_role_name(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("-", " ").replace("_", " ").upper()


def _completion_percentage(completed_tasks: int, total_tasks: int) -> float:
    """Return completed/total; overdue and late are independent diagnostics."""
    if total_tasks <= 0:
        return 0.0
    return round((completed_tasks / total_tasks) * 100, 2)


@unified_router.get(
    "/summary",
    response_model=DashboardSummaryResponse,
    tags=["Unified Dashboard"],
    responses={400: {"description": "Bad Request"}},
)
async def fetch_unified_dashboard_summary(
    period_type: PeriodType = Query(PeriodType.MONTHLY),
    period_start: datetime | None = Query(None),
    period_end: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return one role-scoped aggregation payload for dashboard consumers."""
    scope_user_ids = await _get_scope_user_ids(db, current_user)
    if scope_user_ids is None:
        scope_user_ids = list(
            (
                await db.execute(
                    select(User.id).where(
                        User.is_deleted.is_(False),
                        User.is_active.is_(True),
                    )
                )
            ).scalars().all()
        )
    else:
        scope_user_ids = list(dict.fromkeys(scope_user_ids))

    membership_department_ids = list(
        (
            await db.execute(
                select(UserDepartment.department_id)
                .where(
                    UserDepartment.user_id == current_user.id,
                    UserDepartment.is_deleted.is_(False),
                )
                .order_by(UserDepartment.department_id)
            )
        ).scalars().all()
    )
    role_name = _normalize_role_name(
        getattr(getattr(current_user, "role", None), "name", None)
    )
    if SUPER_ADMIN_ROLE_MARKER in role_name or role_name == "HR":
        department_ids = list(
            (
                await db.execute(
                    select(Department.id).where(Department.is_deleted.is_(False))
                )
            ).scalars().all()
        )
    else:
        department_ids = membership_department_ids

    try:
        return await build_dashboard_summary(
            db,
            current_user,
            scope_user_ids=list(scope_user_ids),
            department_ids=department_ids,
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _get_executive_scope(
    db: AsyncSession, current_user: User
) -> tuple[list[UUID] | None, list[UUID] | None, bool]:
    role_name = _normalize_role_name(
        getattr(getattr(current_user, "role", None), "name", None)
    )
    memberships = (
        await db.execute(
            select(UserDepartment.department_id, UserDepartment.user_level)
            .join(Department, Department.id == UserDepartment.department_id)
            .where(
                UserDepartment.user_id == current_user.id,
                UserDepartment.is_deleted.is_(False),
                Department.is_deleted.is_(False),
            )
        )
    ).all()
    levels = {
        str(row.user_level or "")
        .strip()
        .upper()
        .replace("LEVEL ", "L")
        for row in memberships
    }
    if SUPER_ADMIN_ROLE_MARKER in role_name or role_name == "HR":
        visible_departments = None
    else:
        visible_departments = list(
            dict.fromkeys(row.department_id for row in memberships)
        )
    manager_scope = (
        "ADMIN" in role_name
        or role_name == "HR"
        or bool(levels.intersection({"L0", "L1"}))
        or SUPER_ADMIN_ROLE_MARKER in role_name
    )
    scope_user_ids = await _get_scope_user_ids(db, current_user)
    if scope_user_ids is not None:
        if visible_departments:
            members = (
                await db.execute(
                    select(UserDepartment.user_id)
                    .join(User, User.id == UserDepartment.user_id)
                    .where(
                        UserDepartment.department_id.in_(visible_departments),
                        UserDepartment.is_deleted.is_(False),
                        User.is_deleted.is_(False),
                        User.is_active.is_(True),
                    )
                )
            ).scalars().all()
            permitted_members = set(members)
            scope_user_ids = list(
                dict.fromkeys(
                    user_id
                    for user_id in scope_user_ids
                    if user_id in permitted_members
                )
            )
        else:
            scope_user_ids = (
                [current_user.id]
                if current_user.id in scope_user_ids
                else []
            )
    return (
        scope_user_ids,
        visible_departments,
        manager_scope,
    )


@unified_router.get(
    "/executive/overview",
    tags=["Executive KPI Intelligence"],
    responses={404: {"description": "Not Found"}},
)
async def fetch_executive_overview(
    period_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ExecutiveOverview:
    """Return read-only KPI intelligence within the existing dashboard scope."""
    scope_user_ids, department_ids, show_departments = await _get_executive_scope(
        db, current_user
    )
    try:
        return await build_executive_overview(
            db,
            scope_user_ids=scope_user_ids,
            department_ids=department_ids,
            show_departments=show_departments,
            period_id=period_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@unified_router.get(
    "/executive/employees/{user_id}",
    tags=["Executive KPI Intelligence"],
    responses={404: {"description": "Not Found"}},
)
async def fetch_executive_employee_detail(
    user_id: UUID,
    period_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ExecutiveEmployeeDetail:
    """Return persisted KPI details only for a user in the viewer's dashboard scope."""
    scope_user_ids, department_ids, _ = await _get_executive_scope(db, current_user)
    detail = await get_executive_employee_detail(
        db,
        user_id=user_id,
        period_id=period_id,
        scope_user_ids=scope_user_ids,
        department_ids=department_ids,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="KPI result not found.")
    return detail


@unified_router.get(
    "/executive/employees",
    tags=["Executive KPI Intelligence"],
)
async def fetch_executive_employee_results(
    period_id: UUID = Query(...),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ExecutiveEmployeePage:
    """List evaluated employees alphabetically within the viewer's dashboard scope."""
    scope_user_ids, department_ids, _ = await _get_executive_scope(db, current_user)
    return await list_executive_employee_results(
        db,
        period_id=period_id,
        scope_user_ids=scope_user_ids,
        department_ids=department_ids,
        offset=offset,
        limit=limit,
    )


async def _get_scope_user_ids(
    db: AsyncSession, current_user: User
) -> list[UUID] | None:
    role_name = _normalize_role_name(getattr(getattr(current_user, "role", None), "name", None))
    if SUPER_ADMIN_ROLE_MARKER in role_name or "SUPERADMIN" in role_name:
        return None

    if "ADMIN" in role_name:
        dept_stmt = select(UserDepartment.department_id).where(
            UserDepartment.user_id == current_user.id,
            UserDepartment.is_deleted.is_(False),
        )
        department_ids = (await db.execute(dept_stmt)).scalars().all()
        if not department_ids:
            return [current_user.id]
        user_stmt = select(UserDepartment.user_id).where(
            UserDepartment.department_id.in_(department_ids),
            UserDepartment.is_deleted.is_(False),
            UserDepartment.user_id.in_(
                select(User.id).where(
                    User.is_deleted.is_(False),
                    User.is_active.is_(True),
                )
            ),
        )
        return (await db.execute(user_stmt)).scalars().all()

    if "LEVEL 0" in role_name or "L0" in role_name or "LEVEL0" in role_name:
        dept_stmt = select(UserDepartment.department_id).where(
            UserDepartment.user_id == current_user.id,
            UserDepartment.is_deleted.is_(False),
        )
        department_ids = (await db.execute(dept_stmt)).scalars().all()
        if not department_ids:
            return [current_user.id]
        user_stmt = select(UserDepartment.user_id).where(
            UserDepartment.department_id.in_(department_ids),
            UserDepartment.is_deleted.is_(False),
            UserDepartment.user_id.in_(
                select(User.id).where(
                    User.is_deleted.is_(False),
                    User.is_active.is_(True),
                )
            ),
        )
        return (await db.execute(user_stmt)).scalars().all()

    if "LEVEL 1" in role_name or "L1" in role_name or "LEVEL1" in role_name:
        dept_stmt = select(UserDepartment.department_id).where(
            UserDepartment.user_id == current_user.id,
            UserDepartment.is_deleted.is_(False),
        )
        department_ids = (await db.execute(dept_stmt)).scalars().all()
        if not department_ids:
            return [current_user.id]
        user_stmt = select(UserDepartment.user_id).where(
            UserDepartment.department_id.in_(department_ids),
            UserDepartment.user_id != current_user.id,
            UserDepartment.is_deleted.is_(False),
            UserDepartment.user_id.in_(
                select(User.id).where(
                    User.is_deleted.is_(False),
                    User.is_active.is_(True),
                )
            ),
        )
        allowed_user_ids = (await db.execute(user_stmt)).scalars().all()
        return list(dict.fromkeys(allowed_user_ids + [current_user.id]))

    # Department User accounts carry their operational level on the active
    # department membership rather than in the coarse role name.
    level_rows = (
        await db.execute(
            select(UserDepartment.user_level).where(
                UserDepartment.user_id == current_user.id,
                UserDepartment.is_deleted.is_(False),
            )
        )
    ).scalars().all()
    normalized_levels = {
        str(level).upper().replace("LEVEL ", "L") for level in level_rows
    }
    if normalized_levels.intersection({"L0", "L1"}):
        department_ids = (
            await db.execute(
                select(UserDepartment.department_id).where(
                    UserDepartment.user_id == current_user.id,
                    UserDepartment.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        user_stmt = select(UserDepartment.user_id).where(
            UserDepartment.department_id.in_(department_ids),
            UserDepartment.is_deleted.is_(False),
            UserDepartment.user_id.in_(
                select(User.id).where(
                    User.is_deleted.is_(False),
                    User.is_active.is_(True),
                )
            ),
        )
        allowed_user_ids = (await db.execute(user_stmt)).scalars().all()
        return list(dict.fromkeys(list(allowed_user_ids) + [current_user.id]))

    return [current_user.id]


async def _count_tasks(db: AsyncSession, base_filters: list, status_filters: list) -> int:
    stmt = select(func.count(Task.id)).where(*base_filters, Task.status.in_(status_filters))
    return (await db.execute(stmt)).scalar() or 0


async def _get_dashboard_task_summary(db: AsyncSession, current_user: User) -> dict[str, Any]:
    scope_user_ids = await _get_scope_user_ids(db, current_user)
    base_filters = [Task.is_deleted == False]
    if scope_user_ids is not None:
        base_filters.append(Task.owner_id.in_(scope_user_ids))

    total_stmt = select(func.count(Task.id)).where(*base_filters)
    total_tasks = (await db.execute(total_stmt)).scalar() or 0

    assigned_tasks = await _count_tasks(db, base_filters, [TaskStatus.DRAFT])
    in_progress_tasks = await _count_tasks(db, base_filters, [TaskStatus.PENDING_L1, TaskStatus.PENDING_L0])
    submitted_tasks = in_progress_tasks
    pending_approval_tasks = in_progress_tasks
    approved_tasks = (await db.execute(select(func.count(Task.id)).where(*base_filters, Task.status == TaskStatus.APPROVED))).scalar() or 0
    rejected_tasks = (await db.execute(select(func.count(Task.id)).where(*base_filters, Task.status == TaskStatus.REJECTED))).scalar() or 0
    completed_tasks = (await db.execute(select(func.count(Task.id)).where(*base_filters, Task.status == TaskStatus.COMPLETED))).scalar() or 0
    pending_tasks = await _count_tasks(db, base_filters, [TaskStatus.DRAFT, TaskStatus.PENDING_L1, TaskStatus.PENDING_L0])

    overdue_stmt = select(func.count(Task.id)).where(
        *base_filters,
        Task.due_date.is_not(None),
        Task.due_date < datetime.now(UTC),
        Task.status.not_in([TaskStatus.COMPLETED, TaskStatus.APPROVED, TaskStatus.REJECTED])
    )
    overdue_tasks = (await db.execute(overdue_stmt)).scalar() or 0

    late_stmt = select(func.count(Task.id)).where(
        *base_filters,
        Task.is_late == True,
    )
    late_tasks = (await db.execute(late_stmt)).scalar() or 0

    if scope_user_ids is not None:
        kpi_stmt = select(func.avg(KPIRecord.percentage)).where(KPIRecord.user_id.in_(scope_user_ids))
    else:
        kpi_stmt = select(func.avg(KPIRecord.percentage))
    kpi_value = (await db.execute(kpi_stmt)).scalar() or 0.0

    summary = {
        "total_tasks": total_tasks,
        "assigned_tasks": assigned_tasks,
        "in_progress_tasks": in_progress_tasks,
        "completed_tasks": completed_tasks,
        "pending_tasks": pending_tasks,
        "submitted_tasks": submitted_tasks,
        "pending_approval_tasks": pending_approval_tasks,
        "approved_tasks": approved_tasks,
        "rejected_tasks": rejected_tasks,
        "overdue_tasks": overdue_tasks,
        "late_tasks": late_tasks,
        "current_kpi_score": round(float(kpi_value), 2),
        # Completion is based only on mutually exclusive lifecycle states.
        # Overdue and late are secondary diagnostics and are not added here.
        "completion_percentage": _completion_percentage(completed_tasks, total_tasks),
    }
    # Backward compatibility with legacy dashboard keys.
    summary.update({
        "completed": completed_tasks,
        "pending": pending_tasks,
        "approved": approved_tasks,
        "rejected": rejected_tasks,
        "assigned": assigned_tasks,
        "in_progress": in_progress_tasks,
        "submitted": submitted_tasks,
        "pending_approval": pending_approval_tasks,
    })
    return summary


@superadmin_router.get("/summary/role-scope", tags=["Dashboard"])
async def fetch_dashboard_role_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_checker),
) -> dict[str, Any]:
    """Return a role-scoped summary consistent with the project dashboard data model."""
    summary = await _get_dashboard_task_summary(db, current_user)
    return {"summary": summary}


@superadmin_router.get("/process-tracking", tags=["Dashboard"])
async def fetch_process_tracking_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_checker),
) -> dict[str, Any]:
    """Return a zero-safe tracking summary with explicit status and overdue/late counts."""
    scope_user_ids = await _get_scope_user_ids(db, current_user)
    filters = [Task.is_deleted == False]
    if scope_user_ids is not None:
        filters.append(Task.owner_id.in_(scope_user_ids))

    counts = {
        "TOTAL_TASKS": (await db.execute(select(func.count(Task.id)).where(*filters))).scalar() or 0,
        "DRAFT": (await db.execute(select(func.count(Task.id)).where(*filters, Task.status == TaskStatus.DRAFT))).scalar() or 0,
        "PENDING_L1": (await db.execute(select(func.count(Task.id)).where(*filters, Task.status == TaskStatus.PENDING_L1))).scalar() or 0,
        "PENDING_L0": (await db.execute(select(func.count(Task.id)).where(*filters, Task.status == TaskStatus.PENDING_L0))).scalar() or 0,
        "PENDING_APPROVAL": (await db.execute(select(func.count(Task.id)).where(*filters, Task.status.in_([TaskStatus.PENDING_L1, TaskStatus.PENDING_L0])))).scalar() or 0,
        "APPROVED": (await db.execute(select(func.count(Task.id)).where(*filters, Task.status == TaskStatus.APPROVED))).scalar() or 0,
        "REJECTED": (await db.execute(select(func.count(Task.id)).where(*filters, Task.status == TaskStatus.REJECTED))).scalar() or 0,
        "COMPLETED": (await db.execute(select(func.count(Task.id)).where(*filters, Task.status == TaskStatus.COMPLETED))).scalar() or 0,
        "ASSIGNED": (await db.execute(select(func.count(Task.id)).where(*filters, Task.status == TaskStatus.DRAFT))).scalar() or 0,
        "IN_PROGRESS": (await db.execute(select(func.count(Task.id)).where(*filters, Task.status.in_([TaskStatus.PENDING_L1, TaskStatus.PENDING_L0])))).scalar() or 0,
        "SUBMITTED": (await db.execute(select(func.count(Task.id)).where(*filters, Task.status.in_([TaskStatus.PENDING_L1, TaskStatus.PENDING_L0])))).scalar() or 0,
        "OVERDUE": (await db.execute(select(func.count(Task.id)).where(*filters, Task.due_date.is_not(None),         Task.due_date < datetime.now(UTC), Task.status.not_in([TaskStatus.COMPLETED, TaskStatus.APPROVED, TaskStatus.REJECTED])))).scalar() or 0,
        "LATE": (await db.execute(select(func.count(Task.id)).where(*filters, Task.is_late == True))).scalar() or 0,
    }

    counts.update({
        "Draft": counts["DRAFT"],
        "Pending L1 Approval": counts["PENDING_L1"],
        "Pending L0 Approval": counts["PENDING_L0"],
        "Approved": counts["APPROVED"],
        "Rejected": counts["REJECTED"],
        "Completed": counts["COMPLETED"],
        "Assigned": counts["ASSIGNED"],
        "In Progress": counts["IN_PROGRESS"],
        "Submitted": counts["SUBMITTED"],
        "Pending Approval": counts["PENDING_APPROVAL"],
        "Overdue": counts["OVERDUE"],
        "Late": counts["LATE"],
    })

    return {
        "tracking_metrics": counts,
        "recent_process_activities": [],
        "status": "ok",
    }


async def _get_admin_scope_departments(db: AsyncSession, current_user: User) -> list[str] | None:
    role_name = _normalize_role_name(getattr(getattr(current_user, "role", None), "name", None))
    if SUPER_ADMIN_ROLE_MARKER in role_name:
        return None

    dept_ids = (await db.execute(
        select(UserDepartment.department_id).where(
            UserDepartment.user_id == current_user.id,
            UserDepartment.is_deleted == False,
        )
    )).scalars().all()
    return [str(dept_id) for dept_id in dept_ids] if dept_ids else []


async def _admin_user_counts(
    db: AsyncSession, current_user: User, department_ids: list[str] | None
) -> tuple[int, int]:
    user_filters = [User.is_deleted == False]
    if department_ids is not None and department_ids:
        user_filters.append(User.id.in_(
            select(UserDepartment.user_id).where(
                UserDepartment.department_id.in_(department_ids),
                UserDepartment.is_deleted == False,
            )
        ))
    elif department_ids == []:
        user_filters.append(User.id == current_user.id)
    user_counts = (
        await db.execute(
            select(
                func.count(User.id).label("total_users"),
                func.count(User.id).filter(User.is_active == True).label("active_users"),
            ).where(*user_filters)
        )
    ).one()
    return int(user_counts.total_users or 0), int(user_counts.active_users or 0)


async def _admin_department_count(
    db: AsyncSession, department_ids: list[str] | None
) -> int:
    if department_ids is None:
        return (
            await db.execute(
                select(func.count(Department.id)).where(Department.is_deleted == False)
            )
        ).scalar() or 0
    return len(department_ids)


async def _admin_task_counts(
    db: AsyncSession, current_user: User, department_ids: list[str] | None
):
    task_filters = [Task.is_deleted == False]
    if department_ids:
        task_filters.append(Task.owner_id.in_(
            select(UserDepartment.user_id).where(
                UserDepartment.department_id.in_(department_ids),
                UserDepartment.is_deleted == False,
            )
        ))
    elif department_ids == []:
        task_filters.append(Task.owner_id == current_user.id)
    return (
        await db.execute(
            select(
                func.count(Task.id).label("total_tasks"),
                func.count(Task.id)
                .filter(Task.status.in_([TaskStatus.PENDING_L1, TaskStatus.PENDING_L0]))
                .label("pending_approvals"),
                func.count(Task.id).filter(Task.status == TaskStatus.COMPLETED).label("completed_tasks"),
                func.count(Task.id).filter(Task.status == TaskStatus.APPROVED).label("approved_tasks"),
                func.count(Task.id).filter(Task.status == TaskStatus.REJECTED).label("rejected_tasks"),
                func.count(Task.id)
                .filter(
                    Task.due_date.is_not(None),
                    Task.due_date < datetime.now(UTC),
                    Task.status.not_in(
                        [TaskStatus.COMPLETED, TaskStatus.APPROVED, TaskStatus.REJECTED]
                    ),
                )
                .label("overdue_tasks"),
                func.count(Task.id).filter(Task.is_late == True).label("late_tasks"),
                func.count(Task.id).filter(Task.status == TaskStatus.DRAFT).label("assigned_tasks"),
                func.count(Task.id)
                .filter(
                    Task.status.in_(
                        [TaskStatus.DRAFT, TaskStatus.PENDING_L1, TaskStatus.PENDING_L0]
                    )
                )
                .label("pending_tasks"),
            ).where(*task_filters)
        )
    ).one()


async def _admin_average_kpi(
    db: AsyncSession, current_user: User, department_ids: list[str] | None
):
    kpi_filters = [KPIRecord.percentage.is_not(None), KPIRecord.is_deleted == False]
    if department_ids:
        kpi_filters.append(KPIRecord.department_id.in_(department_ids))
    elif department_ids == []:
        kpi_filters.append(KPIRecord.user_id == current_user.id)
    return (
        await db.execute(select(func.avg(KPIRecord.percentage)).where(*kpi_filters))
    ).scalar() or 0.0


async def _admin_position_count(
    db: AsyncSession, department_ids: list[str] | None
) -> int:
    if department_ids is None:
        return (
            await db.execute(
                select(func.count(Position.id)).where(Position.is_active == True)
            )
        ).scalar() or 0
    if department_ids:
        return (
            await db.execute(
                select(func.count(Position.id)).where(
                    Position.department_id.in_(department_ids),
                    Position.is_active == True,
                )
            )
        ).scalar() or 0
    return 0


async def _admin_users_by_department(
    db: AsyncSession, department_ids: list[str] | None
) -> list[dict[str, Any]]:
    dept_stmt = (
        select(Department.name, func.count(UserDepartment.user_id).label("user_count"))
        .select_from(Department)
        .outerjoin(
            UserDepartment,
            (UserDepartment.department_id == Department.id)
            & (UserDepartment.is_deleted == False)
            & (UserDepartment.user_id.in_(select(User.id).where(User.is_deleted == False))),
        )
        .where(Department.is_deleted == False)
    )
    if department_ids:
        dept_stmt = dept_stmt.where(Department.id.in_(department_ids))
    elif department_ids == []:
        dept_stmt = dept_stmt.where(false())
    dept_stmt = dept_stmt.group_by(Department.name).order_by(
        func.count(UserDepartment.user_id).desc()
    )
    return [
        {"label": name, "value": int(count or 0)}
        for name, count in (await db.execute(dept_stmt)).all()
    ]


async def _admin_kpi_by_department(
    db: AsyncSession, department_ids: list[str] | None
) -> list[dict[str, Any]]:
    dept_kpi_stmt = select(
        Department.name,
        func.avg(KPIRecord.percentage).label("average_kpi")
    ).outerjoin(
        KPIRecord,
        (KPIRecord.department_id == Department.id)
        & (KPIRecord.is_deleted == False)
        & (KPIRecord.percentage.is_not(None)),
    ).where(Department.is_deleted == False)
    if department_ids:
        dept_kpi_stmt = dept_kpi_stmt.where(Department.id.in_(department_ids))
    elif department_ids == []:
        dept_kpi_stmt = dept_kpi_stmt.where(false())
    dept_kpi_stmt = dept_kpi_stmt.group_by(Department.name).order_by(
        func.avg(KPIRecord.percentage).desc()
    )
    return [
        {"label": name, "value": round(float(avg or 0.0), 2)}
        for name, avg in (await db.execute(dept_kpi_stmt)).all()
    ]


@admin_router.get(
    "/summary",
    tags=["Admin Dashboard"],
    responses={403: {"description": "Forbidden"}},
)
async def fetch_admin_dashboard_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_checker),
) -> dict[str, Any]:
    """Return an admin-scoped summary with actual task, user, KPI, and department data."""
    role_name = _normalize_role_name(getattr(getattr(current_user, "role", None), "name", None))
    if "ADMIN" not in role_name and "SYSTEM ADMIN" not in role_name:
        raise HTTPException(status_code=403, detail="Access Denied. You do not have permission to perform this action.")

    department_ids = await _get_admin_scope_departments(db, current_user)
    total_users, active_users = await _admin_user_counts(db, current_user, department_ids)
    department_count = await _admin_department_count(db, department_ids)
    task_counts = await _admin_task_counts(db, current_user, department_ids)
    total_tasks = int(task_counts.total_tasks or 0)
    pending_approvals = int(task_counts.pending_approvals or 0)
    completed_tasks = int(task_counts.completed_tasks or 0)
    rejected_tasks = int(task_counts.rejected_tasks or 0)
    overdue_tasks = int(task_counts.overdue_tasks or 0)
    late_tasks = int(task_counts.late_tasks or 0)
    average_kpi = await _admin_average_kpi(db, current_user, department_ids)
    position_count = await _admin_position_count(db, department_ids)
    users_by_department = await _admin_users_by_department(db, department_ids)

    task_summary = {
        "total": total_tasks,
        "assigned": int(task_counts.assigned_tasks or 0),
        "pending": int(task_counts.pending_tasks or 0),
        "approved": int(task_counts.approved_tasks or 0),
        "completed": completed_tasks,
        "rejected": rejected_tasks,
        "overdue": overdue_tasks,
        "late": late_tasks,
        "completion_percentage": _completion_percentage(completed_tasks, total_tasks),
    }

    kpi_by_department = await _admin_kpi_by_department(db, department_ids)

    # KPIRecord stores an aggregate score for a formula/period, not a
    # per-category result. Task.category is an operational label and cannot be
    # presented as KPI performance. Keep this explicitly unavailable until
    # category-level scoring results exist rather than reporting task counts as
    # performance.
    category_performance = []

    return {
        "metrics": {
            "total_users": total_users,
            "active_users": active_users,
            "departments": department_count,
            "positions": position_count,
            "total_tasks": total_tasks,
            "pending_approvals": pending_approvals,
            "completed_tasks": completed_tasks,
            "overdue_tasks": overdue_tasks,
            "average_kpi": round(float(average_kpi or 0.0), 2),
        },
        "users_by_department": users_by_department,
        "task_summary": task_summary,
        "kpi_by_department": kpi_by_department,
        "category_performance": category_performance,
        "category_performance_status": "unavailable",
        "status": "ok",
    }


@superadmin_router.get("/summary", tags=["Super Admin Dashboard"])
async def fetch_superadmin_dashboard_data(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_checker) # 🌟 ဤနေရာတွင် Token နှင့် Role ကို စစ်ဆေးမည်
) -> dict[str, Any]:
    
    # 1. Total Active Users (exclude deleted/trashed users to match User Management)
    users_count = await db.execute(
        select(func.count(User.id)).where(User.is_deleted == False, User.is_active == True)
    )
    total_users = users_count.scalar() or 0

    # 2. Total Active Departments (exclude deleted/trashed departments)
    depts_count = await db.execute(
        select(func.count(Department.id)).where(Department.is_deleted == False)
    )
    total_depts = depts_count.scalar() or 0

    # 3. Active Alerts (Unread Notifications)
    alerts_count = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.is_read.is_(False),
            Notification.is_deleted.is_(False),
        )
    )
    active_alerts = alerts_count.scalar() or 0

    # 4. Total KPIs Evaluated
    kpis_count = await db.execute(select(func.count(KPIRecord.id)).where(KPIRecord.is_deleted == False))
    total_kpis = kpis_count.scalar() or 0

    # 5. Completed KPIs
    completed_kpis_stmt = select(func.count(KPIRecord.id)).where(
        KPIRecord.is_deleted == False,
        KPIRecord.status_bucket.in_([KPIStatus.EXCELLENT, KPIStatus.VERY_GOOD])
    )
    completed_kpis_count = await db.execute(completed_kpis_stmt)
    completed_kpis = completed_kpis_count.scalar() or 0

    # =========================================================
    # 🌟 6. Recent Alerts List (BUG & TIMEZONE FIXES)
    # =========================================================
    recent_alerts_stmt = (
        select(Notification, User)
        .outerjoin(User, Notification.user_id == User.id)
        .where(Notification.is_read.is_(False), Notification.is_deleted.is_(False))
        .order_by(Notification.created_at.desc())
        .limit(6)
    )
    recent_alerts_req = await db.execute(recent_alerts_stmt)
    
    yangon_tz = APPLICATION_TIMEZONE
    recent_alerts = []
    
    for n, u in recent_alerts_req.all():
        # 🛡️ Convert Database Naive UTC to Asia/Yangon Local Time
        local_time = n.created_at.replace(tzinfo=UTC).astimezone(yangon_tz)
        
        # 🛡️ Extract actual user name or fallback to System
        user_name = f"{u.first_name} {u.last_name}" if u else "System Alert"
        
        recent_alerts.append({
            "name": user_name,
            "title": n.title, 
            "message": n.message, # 🛡️ Ensures detailed context is sent to UI
            "time": local_time.strftime("%d %b %Y, %I:%M %p")
        })

    # 7. Top Performing Departments
    top_depts_stmt = (
        select(
            Department.name,
            func.avg(KPIRecord.percentage).label("avg_completion")
        )
        .join(KPIRecord, (Department.id == KPIRecord.department_id) & (KPIRecord.is_deleted == False))
        .where(Department.is_deleted == False)
        .group_by(Department.name)
        .order_by(func.avg(KPIRecord.percentage).desc())
        .limit(5)
    )
    top_depts_req = await db.execute(top_depts_stmt)
    
    top_departments = []
    for row in top_depts_req.all():
        top_departments.append({
            "name": row[0],
            "completion": round(row[1] or 0.0, 1),
            "avg_score": round(row[1] or 0.0, 2),
            "trend": 0.0 
        })

    trend_stmt = (
        select(
            func.date(KPIRecord.period_start).label("period"),
            func.avg(KPIRecord.percentage).label("average")
        )
        .where(KPIRecord.period_start.is_not(None), KPIRecord.is_deleted == False)
        .group_by(func.date(KPIRecord.period_start))
        .order_by(func.date(KPIRecord.period_start).desc())
        .limit(7)
    )
    trend_rows = (await db.execute(trend_stmt)).all()
    trend_rows.reverse()
    trend = {
        "labels": [row.period.strftime("%d %b") if hasattr(row.period, "strftime") else str(row.period) for row in trend_rows],
        "data": [round(float(row.average or 0.0), 2) for row in trend_rows],
    }

    return {
        "metrics": {
            "total_users": total_users,
            "total_departments": total_depts,
            "total_kpis": total_kpis, 
            "completed_kpis": completed_kpis, 
            "active_alerts": active_alerts
        },
        "recent_alerts": recent_alerts,
        "top_departments": top_departments,
        "trend": trend
    }
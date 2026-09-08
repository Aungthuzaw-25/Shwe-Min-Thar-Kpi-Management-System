from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import pytz # 🌟 Added for Timezone Conversion
from app.core.timezone import APPLICATION_TIMEZONE # 🌟 Added for Timezone Conversion

from app.database.connection import get_db
from app.modules.users.models import User, Position
from app.modules.departments.models import Department, UserDepartment
from app.modules.notifications.models import Notification
from app.modules.kpi.models import KPIRecord, KPIStatus
from app.modules.tasks.models.task import Task, TaskStatus

# 🌟 လုံခြုံရေးအတွက် Role Checker ကို Import လုပ်ခြင်း
from app.modules.users.router import admin_checker 

router = APIRouter()
superadmin_router = APIRouter()
admin_router = APIRouter()


def _normalize_role_name(value: Optional[str]) -> str:
    if not value:
        return ""
    return value.replace("-", " ").replace("_", " ").upper()


async def _get_scope_user_ids(db: AsyncSession, current_user: User) -> Optional[List[int]]:
    role_name = _normalize_role_name(getattr(getattr(current_user, "role", None), "name", None))
    if "SUPER ADMIN" in role_name or "SUPERADMIN" in role_name:
        return None

    if "ADMIN" in role_name:
        dept_stmt = select(UserDepartment.department_id).where(UserDepartment.user_id == current_user.id)
        department_ids = (await db.execute(dept_stmt)).scalars().all()
        if not department_ids:
            return [current_user.id]
        user_stmt = select(UserDepartment.user_id).where(UserDepartment.department_id.in_(department_ids))
        return (await db.execute(user_stmt)).scalars().all()

    if "LEVEL 0" in role_name or "L0" in role_name or "LEVEL0" in role_name:
        dept_stmt = select(UserDepartment.department_id).where(UserDepartment.user_id == current_user.id)
        department_ids = (await db.execute(dept_stmt)).scalars().all()
        if not department_ids:
            return [current_user.id]
        user_stmt = select(UserDepartment.user_id).where(UserDepartment.department_id.in_(department_ids))
        return (await db.execute(user_stmt)).scalars().all()

    if "LEVEL 1" in role_name or "L1" in role_name or "LEVEL1" in role_name:
        dept_stmt = select(UserDepartment.department_id).where(UserDepartment.user_id == current_user.id)
        department_ids = (await db.execute(dept_stmt)).scalars().all()
        if not department_ids:
            return [current_user.id]
        user_stmt = select(UserDepartment.user_id).where(
            UserDepartment.department_id.in_(department_ids),
            UserDepartment.user_id != current_user.id,
        )
        allowed_user_ids = (await db.execute(user_stmt)).scalars().all()
        return list(dict.fromkeys(allowed_user_ids + [current_user.id]))

    return [current_user.id]


async def _count_tasks(db: AsyncSession, base_filters: list, status_filters: list) -> int:
    stmt = select(func.count(Task.id)).where(*base_filters, Task.status.in_(status_filters))
    return (await db.execute(stmt)).scalar() or 0


async def _get_dashboard_task_summary(db: AsyncSession, current_user: User) -> Dict[str, Any]:
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
        Task.due_date < datetime.now(timezone.utc),
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
) -> Dict[str, Any]:
    """Return a role-scoped summary consistent with the project dashboard data model."""
    summary = await _get_dashboard_task_summary(db, current_user)
    return {"summary": summary}


@superadmin_router.get("/process-tracking", tags=["Dashboard"])
async def fetch_process_tracking_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_checker),
) -> Dict[str, Any]:
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
        "OVERDUE": (await db.execute(select(func.count(Task.id)).where(*filters, Task.due_date.is_not(None),         Task.due_date < datetime.now(timezone.utc), Task.status.not_in([TaskStatus.COMPLETED, TaskStatus.APPROVED, TaskStatus.REJECTED])))).scalar() or 0,
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


async def _get_admin_scope_departments(db: AsyncSession, current_user: User) -> Optional[List[str]]:
    role_name = _normalize_role_name(getattr(getattr(current_user, "role", None), "name", None))
    if "SUPER ADMIN" in role_name:
        return None

    dept_ids = (await db.execute(
        select(UserDepartment.department_id).where(
            UserDepartment.user_id == current_user.id,
            UserDepartment.is_deleted == False,
        )
    )).scalars().all()
    return [str(dept_id) for dept_id in dept_ids] if dept_ids else []


@admin_router.get("/summary", tags=["Admin Dashboard"])
async def fetch_admin_dashboard_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_checker),
) -> Dict[str, Any]:
    """Return an admin-scoped summary with actual task, user, KPI, and department data."""
    role_name = _normalize_role_name(getattr(getattr(current_user, "role", None), "name", None))
    if "ADMIN" not in role_name and "SYSTEM ADMIN" not in role_name:
        raise HTTPException(status_code=403, detail="Access Denied. You do not have permission to perform this action.")

    department_ids = await _get_admin_scope_departments(db, current_user)

    user_filters = [User.is_deleted == False]
    if department_ids is not None and department_ids:
        user_filters.append(User.id.in_(
            select(UserDepartment.user_id).where(
                UserDepartment.department_id.in_(department_ids),
                UserDepartment.is_deleted == False,
            )
        ))

    user_counts = (
        await db.execute(
            select(
                func.count(User.id).label("total_users"),
                func.count(User.id).filter(User.is_active == True).label("active_users"),
            ).where(*user_filters)
        )
    ).one()
    total_users = int(user_counts.total_users or 0)
    active_users = int(user_counts.active_users or 0)

    if department_ids is None:
        department_count = (await db.execute(select(func.count(Department.id)).where(Department.is_deleted == False))).scalar() or 0
    else:
        department_count = len(department_ids)

    task_filters = [Task.is_deleted == False]
    if department_ids:
        task_filters.append(Task.owner_id.in_(
            select(UserDepartment.user_id).where(
                UserDepartment.department_id.in_(department_ids),
                UserDepartment.is_deleted == False,
            )
        ))

    task_counts = (
        await db.execute(
            select(
                func.count(Task.id).label("total_tasks"),
                func.count(Task.id)
                .filter(Task.status.in_([TaskStatus.PENDING_L1, TaskStatus.PENDING_L0]))
                .label("pending_approvals"),
                func.count(Task.id).filter(Task.status == TaskStatus.COMPLETED).label("completed_tasks"),
                func.count(Task.id).filter(Task.status == TaskStatus.REJECTED).label("rejected_tasks"),
                func.count(Task.id)
                .filter(
                    Task.due_date.is_not(None),
                    Task.due_date < datetime.now(timezone.utc),
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
    total_tasks = int(task_counts.total_tasks or 0)
    pending_approvals = int(task_counts.pending_approvals or 0)
    completed_tasks = int(task_counts.completed_tasks or 0)
    rejected_tasks = int(task_counts.rejected_tasks or 0)
    overdue_tasks = int(task_counts.overdue_tasks or 0)
    late_tasks = int(task_counts.late_tasks or 0)

    kpi_filters = [KPIRecord.percentage.is_not(None), KPIRecord.is_deleted == False]
    if department_ids:
        kpi_filters.append(KPIRecord.department_id.in_(department_ids))
    average_kpi = (await db.execute(select(func.avg(KPIRecord.percentage)).where(*kpi_filters))).scalar() or 0.0

    position_count = 0
    if department_ids is None:
        position_count = (await db.execute(select(func.count(Position.id)).where(Position.is_active == True))).scalar() or 0
    elif department_ids:
        position_count = (await db.execute(select(func.count(Position.id)).where(Position.department_id.in_(department_ids), Position.is_active == True))).scalar() or 0

    users_by_department = []
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
    dept_stmt = dept_stmt.group_by(Department.name).order_by(func.count(UserDepartment.user_id).desc())
    for name, count in (await db.execute(dept_stmt)).all():
        users_by_department.append({"label": name, "value": int(count or 0)})

    task_summary = {
        "assigned": int(task_counts.assigned_tasks or 0),
        "pending": int(task_counts.pending_tasks or 0),
        "completed": completed_tasks,
        "rejected": rejected_tasks,
        "overdue": overdue_tasks,
        "late": late_tasks,
    }

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
    dept_kpi_stmt = dept_kpi_stmt.group_by(Department.name).order_by(func.avg(KPIRecord.percentage).desc())
    kpi_by_department = [{"label": name, "value": round(float(avg or 0.0), 2)} for name, avg in (await db.execute(dept_kpi_stmt)).all()]

    category_stmt = select(Task.category, func.count(Task.id).label("task_count")).where(*task_filters, Task.category.is_not(None)).group_by(Task.category).order_by(func.count(Task.id).desc()).limit(6)
    category_performance = [{"label": category, "value": int(count or 0)} for category, count in (await db.execute(category_stmt)).all()]

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
        "status": "ok",
    }


@superadmin_router.get("/summary", tags=["Super Admin Dashboard"])
async def fetch_superadmin_dashboard_data(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_checker) # 🌟 ဤနေရာတွင် Token နှင့် Role ကို စစ်ဆေးမည်
) -> Dict[str, Any]:
    
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
    alerts_count = await db.execute(select(func.count(Notification.id)).where(Notification.is_read == False))
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
        .where(Notification.is_read == False)
        .order_by(Notification.created_at.desc())
        .limit(6)
    )
    recent_alerts_req = await db.execute(recent_alerts_stmt)
    
    yangon_tz = APPLICATION_TIMEZONE
    recent_alerts = []
    
    for n, u in recent_alerts_req.all():
        # 🛡️ Convert Database Naive UTC to Asia/Yangon Local Time
        local_time = n.created_at.replace(tzinfo=pytz.utc).astimezone(yangon_tz)
        
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
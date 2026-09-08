from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload

from app.core.dependencies import get_current_user, get_permission_map
from app.core.config import settings
from app.database.connection import get_db
from app.modules.departments.models import UserDepartment
from app.modules.users.models import Position, User

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ==============================================================
# 🌟 Security Helper: Role စစ်ဆေးရန် Function အသစ်
# ==============================================================
def verify_role(user: User, allowed_roles: list[str]) -> bool:
    return user.role and user.role.name in allowed_roles


def normalize_user_level(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().upper().replace("LEVEL ", "L")
    return normalized if normalized in {"L0", "L1", "L2"} else None


async def get_employee_context(db: AsyncSession, current_user: User) -> dict:
    result = await db.execute(
        select(UserDepartment)
        .options(joinedload(UserDepartment.department))
        .where(
            UserDepartment.user_id == current_user.id,
            UserDepartment.is_deleted.is_(False),
        )
    )
    user_department = result.scalars().first()

    position_name = None
    if current_user.position_id:
        position_result = await db.execute(
            select(Position.name).where(Position.id == current_user.position_id)
        )
        position_name = position_result.scalar_one_or_none()

    user_level = normalize_user_level(user_department.user_level if user_department else None)
    display_identity = position_name or (
        user_department.department.name if user_department and user_department.department else None
    ) or current_user.role.name

    return {
        "user_role": current_user.role.name,
        "user_display_identity": display_identity,
        "user_name": f"{current_user.first_name} {current_user.last_name}".strip(),
        "user_email": current_user.email,
        "user_id": str(current_user.id),
        "user_level": user_level,
        "department_id": (
            str(user_department.department_id) if user_department else None
        ),
        "department_name": (
            user_department.department.name
            if user_department and user_department.department
            else None
        ),
        "position_name": position_name,
        "permission_map": get_permission_map(current_user),
    }

# ==============================================================
# Super Admin Dashboard Route
# ==============================================================
@router.get("/superadmin/dashboard", include_in_schema=False)
async def render_superadmin_dashboard_view(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")

    if not verify_role(current_user, ['Super Admin']):
        return RedirectResponse(url="/dashboard")

    return templates.TemplateResponse(
        request=request,
        name="dashboard/superadmin.html",
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )


# ==============================================================
# Admin Dashboard Route
# ==============================================================
@router.get("/admin/dashboard", include_in_schema=False)
async def render_admin_dashboard_view(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")

    if not verify_role(current_user, ['Admin', 'System Admin']):
        return RedirectResponse(url="/dashboard")

    employee_context = await get_employee_context(db, current_user)
    return templates.TemplateResponse(
        request=request,
        name="dashboard/admin_dashboard.html",
        context={
            **employee_context,
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email,
        }
    )


# ==============================================================
# General Dashboard Route (Dynamic Routing for L0, L1, L2)
# ==============================================================
@router.get("/dashboard", include_in_schema=False)
async def render_normal_dashboard_view(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")

    role_name = current_user.role.name
    employee_context = await get_employee_context(db, current_user)
    user_level = employee_context["user_level"]

    if role_name in ['Super Admin']:
        return RedirectResponse(url="/superadmin/dashboard")

    if role_name in ['Admin', 'System Admin']:
        return RedirectResponse(url="/admin/dashboard")

    if user_level == "L0":
        return templates.TemplateResponse(
            request=request,
            name="dashboard/l0_dashboard.html",
            context={**employee_context}
        )

    if user_level == "L1":
        return templates.TemplateResponse(
            request=request,
            name="dashboard/l1_dashboard.html",
            context={**employee_context}
        )

    if user_level != "L2":
        return RedirectResponse(url="/profile")

    return templates.TemplateResponse(
        request=request,
        name="dashboard/l2_dashboard.html",
        context={**employee_context}
    )


@router.get("/profile", include_in_schema=False)
async def render_employee_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
    context = await get_employee_context(db, current_user)
    context.update(
        {
            "active_page": "profile",
            "account_status": "Active" if current_user.is_active else "Inactive",
            "created_at": current_user.created_at,
        }
    )
    return templates.TemplateResponse(
        request=request,
        name="dashboard/profile.html",
        context=context,
    )

# ==============================================================
# Users Management Route
# ==============================================================
@router.get("/superadmin/users", include_in_schema=False)
async def render_superadmin_users_view(
    request: Request, 
    current_user: User = Depends(get_current_user)
): 
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
        
    # 🌟 SECURITY FIX: Prevent normal users from accessing user management
    if not verify_role(current_user, ['Super Admin','Admin']):
        return RedirectResponse(url="/dashboard")
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/users.html",
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )

# ==============================================================
# Departments Management Web Route 
# ==============================================================
@router.get("/superadmin/super-admins", include_in_schema=False)
async def render_super_admin_management_view(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
    if not verify_role(current_user, ['Super Admin']):
        return RedirectResponse(url="/dashboard")

    return templates.TemplateResponse(
        request=request,
        name="dashboard/super_admin_management.html",
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )


@router.get("/superadmin/departments", include_in_schema=False)
async def render_departments_view(
    request: Request, 
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
    if not verify_role(current_user, ['Super Admin','Admin']):
        return RedirectResponse(url="/dashboard")
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/departments.html",  
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )

@router.get("/superadmin/permissions", include_in_schema=False)
async def render_permissions_view(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
    if not verify_role(current_user, ['Super Admin', 'Admin']):
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse(
        request=request,
        name="dashboard/permissions.html",
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )

# ==============================================================
# User Profile Web Route 
# ==============================================================
@router.get("/superadmin/users/{user_id}/profile", include_in_schema=False)
async def render_user_profile_view(
    request: Request, 
    user_id: str,
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
    if not verify_role(current_user, ['Super Admin','Admin']):
        return RedirectResponse(url="/dashboard")
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/user_profile.html", 
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email,
            "target_user_id": user_id  
        }
    )
    
# ==============================================================
# KPI Dashboard Web Route (Super Admin & Admin Only)
# ==============================================================
@router.get("/superadmin/kpi", include_in_schema=False)
async def render_kpi_dashboard_view(
    request: Request, 
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
    if not verify_role(current_user, ['Super Admin', 'Admin']):
        return RedirectResponse(url="/dashboard")
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/superadmin_kpi.html", 
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )

# ==============================================================
# KPI Data Import Web Route (Excel Upload) 
# ==============================================================
@router.get("/superadmin/kpi-upload", include_in_schema=False)
async def render_kpi_upload_view(
    request: Request, 
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
    if not verify_role(current_user, ['Super Admin','Admin', 'HR']):
        return RedirectResponse(url="/dashboard")
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/kpi_upload.html",
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )
    
# ==============================================================
# Targets Management Route
# ==============================================================
@router.get("/superadmin/targets", include_in_schema=False)
async def render_targets_view(
    request: Request, 
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
    if not verify_role(current_user, ['Super Admin','Admin']):
        return RedirectResponse(url="/dashboard")
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/target.html", 
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )
    
# ==============================================================
# KPI Reports Web Route
# ==============================================================
@router.get("/superadmin/reports", include_in_schema=False)
async def render_kpi_reports_view(
    request: Request, 
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
    if not verify_role(current_user, ['Super Admin','Admin']):
        return RedirectResponse(url="/dashboard")
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/report.html", 
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )

# ==============================================================
# Positions Management Web Route
# ==============================================================
@router.get("/positions", include_in_schema=False)
async def positions_page(
    request: Request, 
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
        
    if not verify_role(current_user, ['Super Admin','Admin']):
        return RedirectResponse(url="/dashboard")
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/positions.html", 
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )

# ==============================================================
# KPI Periods Management Web Route
# ==============================================================
@router.get("/kpi-periods", include_in_schema=False)
async def kpi_periods_page(
    request: Request, 
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
        
    if not verify_role(current_user, ['Super Admin','Admin']):
        return RedirectResponse(url="/dashboard")
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/kpi_periods.html", 
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )

# ==============================================================
# Action Plans Web Route
# ==============================================================
@router.get("/action-plans", include_in_schema=False)
async def action_plans_page(
    request: Request, 
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/action_plans.html", 
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )

# ==============================================================
# System Settings Web Route
# ==============================================================
@router.get("/system-settings", include_in_schema=False)
async def system_settings_page(
    request: Request, 
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
        
    if not verify_role(current_user, ['Super Admin']):
        return RedirectResponse(url="/dashboard")
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/system_settings.html", 
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )

# ==============================================================
# System Audit Logs Web Route (Super Admin Only)
# ==============================================================
@router.get("/superadmin/audit-logs", include_in_schema=False)
async def render_audit_logs_view(
    request: Request, 
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
        
    if not verify_role(current_user, ['Super Admin']):
        return RedirectResponse(url="/dashboard")
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/audit_logs.html",
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email,
            "timezone": settings.TIMEZONE
        }
    )


@router.get("/superadmin/system-logs", include_in_schema=False)
async def render_system_logs_view(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
    if not verify_role(current_user, ['Super Admin']):
        return RedirectResponse(url="/dashboard")

    return templates.TemplateResponse(
        request=request,
        name="dashboard/system_logs.html",
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email,
            "timezone": settings.TIMEZONE
        }
    )
    
# ==============================================================
# Tasks & Evidence Management Web Route
# ==============================================================
@router.get("/tasks", include_in_schema=False)
async def render_tasks_management_view(
    request: Request, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
        
    employee_context = await get_employee_context(db, current_user)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/tasks.html",
        context={
            **employee_context
        }
    )
    
# ==============================================================
# Process Tracking Web Route
# ==============================================================
@router.get("/superadmin/process-tracking", include_in_schema=False)
async def render_process_tracking_view(
    request: Request, 
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
        
    if not verify_role(current_user, ['Super Admin']):
        return RedirectResponse(url="/dashboard")
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/process_tracking.html",
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )
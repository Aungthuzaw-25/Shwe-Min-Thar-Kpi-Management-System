from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.core.dependencies import get_current_user, get_permission_map
from app.database.connection import get_db
from app.modules.auth.profile_router import get_account
from app.modules.departments.models import Department, UserDepartment
from app.modules.users.avatar import avatar_color_index, avatar_initials, avatar_url
from app.modules.users.models import OAuthAccount, Position, User
from app.modules.system import branding
from app.web.template_support import templates

LOGIN_REDIRECT_URL = "/auth/login"
ROLE_SUPER_ADMIN = "Super Admin"
DASHBOARD_URL = "/dashboard"

router = APIRouter()

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


def build_user_template_context(user: User) -> dict:
    user_name = " ".join(
        part for part in (user.first_name, user.last_name) if part
    ).strip() or "User"
    email = user.email or ""
    avatar = {
        "display_name": user_name,
        "full_name": user_name,
        "name": user_name,
        "avatar_url": avatar_url(user.avatar_path, user.avatar_updated_at),
        "initials": avatar_initials(user_name, email),
        "avatar_color_index": avatar_color_index(user.id) if user.id else 0,
    }
    return {
        "current_user": {
            **avatar,
            "user_id": str(user.id) if user.id else None,
            "email": email,
            "role": user.role.name if user.role else None,
        },
        "user_avatar": avatar,
        "user_email": email,
        "user_name": user_name,
        "user_role": user.role.name if user.role else None,
        "version": settings.VERSION,
    }


async def get_employee_context(db: AsyncSession, current_user: User) -> dict:
    result = await db.execute(
        select(
            UserDepartment.user_level,
            UserDepartment.department_id,
            Department.name.label("department_name"),
            Position.name.label("position_name"),
        )
        .select_from(User)
        .outerjoin(
            UserDepartment,
            (UserDepartment.user_id == User.id)
            & UserDepartment.is_deleted.is_(False),
        )
        .outerjoin(Department, Department.id == UserDepartment.department_id)
        .outerjoin(Position, Position.id == User.position_id)
        .where(User.id == current_user.id)
        .limit(1)
    )
    employee_data = result.first()
    user_level = normalize_user_level(employee_data.user_level if employee_data else None)
    department_id = employee_data.department_id if employee_data else None
    department_name = employee_data.department_name if employee_data else None
    position_name = employee_data.position_name if employee_data else None
    display_identity = position_name or department_name or current_user.role.name

    return {
        **build_user_template_context(current_user),
        "user_role": current_user.role.name,
        "user_display_identity": display_identity,
        "user_name": f"{current_user.first_name} {current_user.last_name}".strip(),
        "user_email": current_user.email,
        "user_id": str(current_user.id),
        "user_level": user_level,
        "department_id": str(department_id) if department_id else None,
        "department_name": department_name,
        "position_name": position_name,
        "permission_map": get_permission_map(current_user),
    }

# ==============================================================
# Super Admin Dashboard Route
# ==============================================================
@router.get("/superadmin/dashboard", include_in_schema=False)
async def render_superadmin_dashboard_view(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url=LOGIN_REDIRECT_URL)

    if not verify_role(current_user, [ROLE_SUPER_ADMIN]):
        return RedirectResponse(url=DASHBOARD_URL)

    return templates.TemplateResponse(
        request=request,
        name="dashboard/superadmin.njk",
        context={**build_user_template_context(current_user),
            "brand": await branding.resolve_brand(db),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)

    if not verify_role(current_user, ['Admin', 'System Admin']):
        return RedirectResponse(url=DASHBOARD_URL)

    employee_context = await get_employee_context(db, current_user)
    employee_context["brand"] = await branding.resolve_brand(db)
    return templates.TemplateResponse(
        request=request,
        name="dashboard/admin_dashboard.njk",
        context={
            **employee_context,
        }
    )


# ==============================================================
# General Dashboard Route (Dynamic Routing for L0, L1, L2)
# ==============================================================
@router.get(DASHBOARD_URL, include_in_schema=False)
async def render_normal_dashboard_view(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url=LOGIN_REDIRECT_URL)

    role_name = current_user.role.name
    employee_context = await get_employee_context(db, current_user)
    employee_context["brand"] = await branding.resolve_brand(db)
    user_level = employee_context["user_level"]

    if role_name in [ROLE_SUPER_ADMIN]:
        return RedirectResponse(url="/superadmin/dashboard")

    if role_name in ['Admin', 'System Admin']:
        return RedirectResponse(url="/admin/dashboard")

    if user_level == "L0":
        return templates.TemplateResponse(
            request=request,
            name="dashboard/l0_dashboard.njk",
            context=employee_context,
        )

    if user_level == "L1":
        return templates.TemplateResponse(
            request=request,
            name="dashboard/l1_dashboard.njk",
            context=employee_context,
        )

    if user_level != "L2":
        return RedirectResponse(url="/profile")

    return templates.TemplateResponse(
        request=request,
        name="dashboard/l2_dashboard.njk",
        context=employee_context,
    )


@router.get("/profile", include_in_schema=False)
async def render_employee_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user or not current_user.role:
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
    context = await get_employee_context(db, current_user)
    identity_rows = (await db.execute(
        select(OAuthAccount).where(
            OAuthAccount.user_id == current_user.id,
            OAuthAccount.revoked_at.is_(None),
        ).order_by(OAuthAccount.provider)
    )).scalars().all()
    connected_accounts = {
        row.provider: {
            "email": row.provider_email,
            "connected_at": row.linked_at or row.created_at,
        }
        for row in identity_rows
    }
    context["brand"] = await branding.resolve_brand(db)
    context.update(
        {
            "active_page": "profile",
            "account_status": "Active" if current_user.is_active else "Inactive",
            "created_at": current_user.created_at,
            "connected_accounts": connected_accounts,
            "local_password_enabled": bool(
                getattr(current_user, "local_password_enabled", True)
            ),
        }
    )
    return templates.TemplateResponse(
        request=request,
        name="dashboard/profile.njk",
        context=context,
    )


@router.get("/account/profile", include_in_schema=False)
async def render_account_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    account_profile = await get_account(db, current_user)
    context = await get_employee_context(db, current_user)
    context["brand"] = await branding.resolve_brand(db)
    context.update(
        {
            "active_page": "profile",
            "account_profile": account_profile,
            "timezone": settings.TIMEZONE,
        }
    )
    template_name = (
        "dashboard/account_profile_admin.njk"
        if account_profile["is_super_admin"]
        else "dashboard/account_profile.njk"
    )
    return templates.TemplateResponse(
        request=request,
        name=template_name,
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
        
    # 🌟 SECURITY FIX: Prevent normal users from accessing user management
    if not verify_role(current_user, [ROLE_SUPER_ADMIN,'Admin']):
        return RedirectResponse(url=DASHBOARD_URL)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/users.njk",
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
    if not verify_role(current_user, [ROLE_SUPER_ADMIN]):
        return RedirectResponse(url=DASHBOARD_URL)

    return templates.TemplateResponse(
        request=request,
        name="dashboard/super_admin_management.njk",
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
    if not verify_role(current_user, [ROLE_SUPER_ADMIN,'Admin']):
        return RedirectResponse(url=DASHBOARD_URL)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/departments.njk",  
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
    if not verify_role(current_user, [ROLE_SUPER_ADMIN, 'Admin']):
        return RedirectResponse(url=DASHBOARD_URL)
    return templates.TemplateResponse(
        request=request,
        name="dashboard/permissions.njk",
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
    if not verify_role(current_user, [ROLE_SUPER_ADMIN,'Admin']):
        return RedirectResponse(url=DASHBOARD_URL)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/user_profile.njk", 
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
    if not verify_role(current_user, [ROLE_SUPER_ADMIN, 'Admin']):
        return RedirectResponse(url=DASHBOARD_URL)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/superadmin_kpi.njk", 
        context={**build_user_template_context(current_user),
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )


@router.get("/superadmin/department-kpi", include_in_schema=False)
async def render_department_kpi_view(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
    if not verify_role(current_user, [ROLE_SUPER_ADMIN, 'Admin']):
        return RedirectResponse(url=DASHBOARD_URL)

    return templates.TemplateResponse(
        request=request,
        name="dashboard/department_kpi.njk",
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
    if not verify_role(current_user, [ROLE_SUPER_ADMIN,'Admin', 'HR']):
        return RedirectResponse(url=DASHBOARD_URL)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/kpi_upload.njk",
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
    if not verify_role(current_user, [ROLE_SUPER_ADMIN,'Admin']):
        return RedirectResponse(url=DASHBOARD_URL)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/target.njk", 
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
    if not verify_role(current_user, [ROLE_SUPER_ADMIN,'Admin']):
        return RedirectResponse(url=DASHBOARD_URL)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/report.njk", 
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
        
    if not verify_role(current_user, [ROLE_SUPER_ADMIN,'Admin']):
        return RedirectResponse(url=DASHBOARD_URL)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/positions.njk", 
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
        
    if not verify_role(current_user, [ROLE_SUPER_ADMIN,'Admin']):
        return RedirectResponse(url=DASHBOARD_URL)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/kpi_periods.njk", 
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/action_plans.njk", 
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
        
    if not verify_role(current_user, [ROLE_SUPER_ADMIN]):
        return RedirectResponse(url=DASHBOARD_URL)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/system_settings.njk", 
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
        
    if not verify_role(current_user, [ROLE_SUPER_ADMIN]):
        return RedirectResponse(url=DASHBOARD_URL)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/audit_logs.njk",
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
    if not verify_role(current_user, [ROLE_SUPER_ADMIN]):
        return RedirectResponse(url=DASHBOARD_URL)

    return templates.TemplateResponse(
        request=request,
        name="dashboard/system_logs.njk",
        context={**build_user_template_context(current_user),
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email,
            "timezone": settings.TIMEZONE
        }
    )


# ==============================================================
# System Health Web Route (Super Admin Only)
# ==============================================================
@router.get("/superadmin/health", include_in_schema=False)
async def render_system_health_view(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    if not current_user or not current_user.role:
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
    if not verify_role(current_user, [ROLE_SUPER_ADMIN]):
        return RedirectResponse(url=DASHBOARD_URL)

    return templates.TemplateResponse(
        request=request,
        name="dashboard/system_health.njk",
        context={**build_user_template_context(current_user),
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
        
    employee_context = await get_employee_context(db, current_user)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/tasks.njk",
        context=employee_context,
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
        return RedirectResponse(url=LOGIN_REDIRECT_URL)
        
    if not verify_role(current_user, [ROLE_SUPER_ADMIN]):
        return RedirectResponse(url=DASHBOARD_URL)
        
    return templates.TemplateResponse(
        request=request, 
        name="dashboard/process_tracking.njk",
        context={**build_user_template_context(current_user),
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email
        }
    )
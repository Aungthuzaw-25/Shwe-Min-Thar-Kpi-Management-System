from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.connection import get_db
from app.modules.users.models import User
from app.web.dashboard_web import get_employee_context, templates

router = APIRouter()


@router.get("/notifications", include_in_schema=False)
async def notifications_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
    context = await get_employee_context(db, current_user)
    context["active_page"] = "notifications"
    context["layout_template"] = (
        "layouts/employee_base.njk"
        if current_user.role.name not in {"Super Admin", "Admin", "System Admin"}
        else "layouts/base.njk"
    )
    return templates.TemplateResponse(
        request=request,
        name="dashboard/notifications.njk",
        context=context,
    )

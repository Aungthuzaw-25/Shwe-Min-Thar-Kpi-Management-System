from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.core.dependencies import get_current_user
from app.modules.users.models import User
from app.web.dashboard_web import templates

router = APIRouter()


@router.get("/notifications", include_in_schema=False)
async def notifications_page(request: Request, current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.role:
        return RedirectResponse(url="/auth/login")
    return templates.TemplateResponse(
        request=request,
        name="dashboard/notifications.html",
        context={
            "user_role": current_user.role.name,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "user_email": current_user.email,
        },
    )

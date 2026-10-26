from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.connection import get_db
from app.modules.system import branding
from app.modules.users.models import User
from app.web.template_support import templates

router = APIRouter()

@router.get("/login", include_in_schema=False)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    # Company trademark + which OAuth providers are configured, from the
    # same source as GET /api/v1/system/branding/public. resolve_brand
    # never raises, so a settings problem cannot break the login page.
    from app.modules.auth.router import oauth_provider_availability

    return templates.TemplateResponse(
        request=request,
        name="auth/login.njk",
        context={
            "brand": await branding.resolve_brand(db),
            "oauth_providers": oauth_provider_availability(),
        },
    )

@router.get('/change-password', include_in_schema=False)
async def change_password_page(
    request: Request,
    # I4: the page used to render for anonymous visitors while its
    # POST already required auth (a form that could never submit).
    # Anonymous requests now 401 here, like every other page route.
    current_user: User = Depends(get_current_user),
):
    return templates.TemplateResponse(
        request=request,
        name="auth/change_password.njk"
    )
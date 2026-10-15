from fastapi import APIRouter, Request

from app.web.template_support import templates

router = APIRouter()

@router.get("/login", include_in_schema=False)
async def login_page(request: Request):
    # FastAPI Version အလိုက် request ကို Keyword Arguments အနေဖြင့် ပေးပို့ရပါမည်
    return templates.TemplateResponse(
        request=request,
        name="auth/login.njk"
    )

@router.get('/change-password', include_in_schema=False)
async def change_password_page(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="auth/change_password.njk"
    )
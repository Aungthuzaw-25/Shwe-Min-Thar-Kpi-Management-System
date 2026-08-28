from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter()

# Initialize Jinja2Templates pointing to your templates folder
templates = Jinja2Templates(directory="templates")

@router.get("/login", include_in_schema=False)
async def login_page(request: Request):
    # FastAPI Version အလိုက် request ကို Keyword Arguments အနေဖြင့် ပေးပို့ရပါမည်
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html"
    )

@router.get('/change-password', include_in_schema=False)
async def change_password_page(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="auth/change_password.html"
    )
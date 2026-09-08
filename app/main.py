import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

# 🌟 Rate Limiter imports
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.database.connection import engine
from app.database.seed import run_seed

# Backend API Routers (DDD Modules)
from app.modules.auth.router import router as auth_router
from app.modules.departments.router import router as dept_router
from app.modules.users.router import router as user_router 
from app.modules.users.position_router import router as position_router 
from app.modules.users.permission_router import router as permission_router 
from app.modules.tasks.router import router as tasks_router
from app.modules.kpi.router import router as kpi_router
from app.modules.kpi.period_router import router as kpi_period_router
from app.modules.kpi.assignment_router import router as kpi_assignment_router
from app.modules.kpi.action_plan_router import router as action_plan_router
from app.modules.kpi import upload_router
from app.modules.targets.router import router as targets_router
from app.modules.kpi.dashboard_router import (
    admin_router as admin_dashboard_api_router,
    superadmin_router as superadmin_dashboard_api_router,
)
from app.modules.notifications.router import router as notifications_router
from app.modules.activity_router import router as activity_router
from app.modules.system.router import router as system_router

# Frontend Web Routers (UI Pages)
from app.web.auth_web import router as auth_web_router
from app.web.dashboard_web import router as dashboard_web_router
from app.modules.activity_web import router as activity_web_router

# Background Jobs & Scheduler
from app.background_jobs.scheduler import start_scheduler

logger = logging.getLogger("app.main")

# ==============================================================================
# 🌟 0. ENVIRONMENT & SECURITY SETUP
# ==============================================================================
# Evaluate production status early so it can be used during FastAPI instantiation
IS_PRODUCTION = getattr(settings, "ENV", "development").lower() == "production"

# Ensure static/upload directories exist before any StaticFiles mount is created.
# This avoids import-time crashes when the app is loaded without the lifespan hook running.
os.makedirs("static/css", exist_ok=True)
os.makedirs("uploads", exist_ok=True)


# ==============================================================================
# 🌟 1. LIFESPAN CONTEXT MANAGER (Modern Replacement for on_event)
# ==============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP LOGIC ---
    logger.info(f"🚀 Initializing {settings.PROJECT_NAME} (v{settings.VERSION})...")
    
    # Static & Upload Folders တည်ဆောက်ခြင်း
    os.makedirs("static/css", exist_ok=True)
    os.makedirs("uploads", exist_ok=True)

    environment_name = (getattr(settings, "ENV", "development") or "development").lower()
    if environment_name in {"development", "test"}:
        try:
            await run_seed()
            logger.info("✅ Demo/test seed state refreshed for local and CI startup.")
        except Exception:
            logger.exception("Demo/test seed refresh failed during startup; continuing without violating app availability.")
    
    scheduler = None
    if settings.ENABLE_SCHEDULER and settings.RUN_SCHEDULER:
        scheduler = start_scheduler()
        logger.info("✅ Background Scheduler started successfully.")
    else:
        logger.info("Background Scheduler is disabled.")
    
    yield  # Application Running
    
    # --- SHUTDOWN LOGIC ---
    logger.info("🛑 Shutting down application...")
    if scheduler:
        scheduler.shutdown(wait=False)
    await engine.dispose()
    logger.info("✅ Database connections closed cleanly.")

# ==============================================================================
# 🌟 2. RATE LIMITER (Redis-Compatible for Multi-Worker Scaling)
# ==============================================================================
redis_url = getattr(settings, "REDIS_URL", None)
if redis_url:
    limiter = Limiter(key_func=get_remote_address, storage_uri=redis_url)
else:
    limiter = Limiter(key_func=get_remote_address)


# ==============================================================================
# 🚀 3. FASTAPI INSTANTIATION
# ==============================================================================
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
    # 🔴 ENTERPRISE SECURITY: Disable Swagger UI & OpenAPI schema in Production
    docs_url=None if IS_PRODUCTION else "/api/docs",
    redoc_url=None if IS_PRODUCTION else "/api/redoc",
    openapi_url=None if IS_PRODUCTION else "/api/openapi.json"
)

# Rate Limiter Exception Handler ချိတ်ဆက်ခြင်း
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ==============================================================================
# 🛡️ 4. SECURITY & PERFORMANCE MIDDLEWARES
# ==============================================================================

# A. GZip Compression (API Response Payload ကို ချုံ့ပေး၍ Speed မြန်စေသည်)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# B. Strict CORS Policy
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in settings.ALLOWED_ORIGINS.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# C. Bank-Grade HTTP Security Headers & Content Security Policy (CSP)
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob: http://localhost:9000 http://minio:9000; "
            "connect-src 'self' https://cdn.jsdelivr.net http://localhost:9000 http://minio:9000; "
            "frame-ancestors 'none';"
        )
        return response

app.add_middleware(SecurityHeadersMiddleware)

# ==============================================================================
# 📂 5. STATIC FILES & STORAGE MOUNTING
# ==============================================================================
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==============================================================================
# 🔌 6. BACKEND API ROUTERS (Domain-Driven Design)
# ==============================================================================
app.include_router(auth_router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(dept_router, prefix="/api/v1/departments", tags=["Departments"])
app.include_router(user_router, prefix="/api/v1/users", tags=["Users"]) 
app.include_router(position_router, prefix="/api/v1", tags=["Positions"]) 
app.include_router(permission_router, prefix="/api/v1", tags=["Permissions"]) 
app.include_router(tasks_router, prefix="/api/v1/tasks", tags=["Tasks"])
app.include_router(kpi_router, prefix="/api/v1/kpi", tags=["KPI"])
app.include_router(kpi_period_router, prefix="/api/v1/kpi", tags=["KPI Periods"])
app.include_router(kpi_assignment_router, prefix="/api/v1/kpi", tags=["KPI Assignments"])
app.include_router(action_plan_router, prefix="/api/v1/kpi", tags=["KPI Action Plans"])
app.include_router(upload_router.router, prefix="/api/v1", tags=["KPI Excel Uploads"])
app.include_router(targets_router, prefix="/api/v1/targets", tags=["Targets"])
app.include_router(superadmin_dashboard_api_router, prefix="/api/v1/superadmin/dashboard", tags=["Super Admin Dashboard"])
app.include_router(admin_dashboard_api_router, prefix="/api/v1/admin/dashboard", tags=["Admin Dashboard"])
app.include_router(notifications_router, prefix="/api/v1/notifications", tags=["Notifications"])
app.include_router(activity_router, prefix="/api/v1")
app.include_router(system_router, prefix="/api/v1", tags=["System Settings"])

# ==============================================================================
# 💻 7. FRONTEND WEB ROUTERS (Jinja2 UI Pages)
# ==============================================================================
app.include_router(auth_web_router, prefix="/auth", tags=["Web Auth"])
app.include_router(dashboard_web_router, tags=["Web Dashboard UI"])
app.include_router(activity_web_router, tags=["Web Notifications UI"])

# ==============================================================================
# 🌐 8. ROOT & HEALTH CHECK ENDPOINTS
# ==============================================================================
@app.get("/", include_in_schema=False)
async def root():
    """အလိုအလျောက် Login Page သို့ ရောက်စေရန် Redirect ပြုလုပ်ခြင်း"""
    return RedirectResponse(url="/auth/login")

@app.get("/health", tags=["System"])
# @limiter.limit("20/minute")  # Disabled for test stability
async def health_check(request: Request):
    """
    Docker Health Check & Kubernetes Liveness Probe အတွက်
    Database connection ပါ တစ်ပါတည်း စစ်ဆေးပေးသော Health Check Endpoint
    """
    db_status = "healthy"
    try:
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = "unhealthy"

    status_code = status.HTTP_200_OK if db_status == "healthy" else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=status_code,
        content={
            "status": db_status,
            "project": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "environment": getattr(settings, "ENV", "development")
        }
    )
import gzip
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import from_url as redis_from_url

# 🌟 Rate Limiter imports
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

# Background Jobs & Scheduler
from app.background_jobs.scheduler import start_scheduler
from app.core.config import settings
from app.database.bootstrap import bootstrap_initial_super_admin
from app.database.connection import engine
from app.middleware.logging_middleware import RequestLoggingMiddleware
from app.models.system import LogLevel
from app.modules.activity_router import router as activity_router
from app.modules.activity_web import router as activity_web_router
from app.modules.auth.profile_router import account_router

# Backend API Routers (DDD Modules)
from app.modules.auth.profile_router import router as auth_profile_router
from app.modules.auth.router import router as auth_router
from app.modules.departments.router import router as dept_router
from app.modules.kpi import upload_router
from app.modules.kpi.action_plan_router import router as action_plan_router
from app.modules.kpi.assignment_router import router as kpi_assignment_router
from app.modules.kpi.dashboard_router import (
    admin_router as admin_dashboard_api_router,
)
from app.modules.kpi.dashboard_router import (
    superadmin_router as superadmin_dashboard_api_router,
)
from app.modules.kpi.dashboard_router import (
    unified_router as unified_dashboard_api_router,
)
from app.modules.kpi.period_router import router as kpi_period_router
from app.modules.kpi.router import router as kpi_router
from app.modules.notifications.router import router as notifications_router
from app.modules.system.router import router as system_router
from app.modules.targets.router import router as targets_router
from app.modules.tasks.router import router as tasks_router
from app.modules.users.permission_router import router as permission_router
from app.modules.users.position_router import router as position_router
from app.modules.users.router import router as user_router
from app.services.system_log_service import record_system_log

# Frontend Web Routers (UI Pages)
from app.web.auth_web import router as auth_web_router
from app.web.dashboard_web import router as dashboard_web_router

logger = logging.getLogger("app.main")
API_V1_PREFIX = "/api/v1"
API_V1_KPI_PREFIX = f"{API_V1_PREFIX}/kpi"

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
    await record_system_log(
        LogLevel.INFO,
        "APPLICATION_STARTUP",
        "Application startup initiated",
        {"component": "application", "event": "startup"},
    )
    
    # Static & Upload Folders တည်ဆောက်ခြင်း
    os.makedirs("static/css", exist_ok=True)
    os.makedirs("uploads", exist_ok=True)

    await bootstrap_initial_super_admin()
    oauth_redis = None
    if settings.OAUTH_ENABLED and settings.REDIS_URL:
        oauth_redis = redis_from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await oauth_redis.ping()
        except Exception:
            await oauth_redis.aclose()
            oauth_redis = None
            logger.warning("OAuth Redis is unavailable; OAuth flows will fail closed.")
    app.state.oauth_redis = oauth_redis

    scheduler = None
    if settings.ENABLE_SCHEDULER and settings.RUN_SCHEDULER:
        scheduler = start_scheduler()
        logger.info("✅ Background Scheduler started successfully.")
    else:
        logger.info("Background Scheduler is disabled.")
    
    yield  # Application Running
    
    # --- SHUTDOWN LOGIC ---
    logger.info("🛑 Shutting down application...")
    await record_system_log(
        LogLevel.INFO,
        "APPLICATION_SHUTDOWN",
        "Application shutdown initiated",
        {"component": "application", "event": "shutdown"},
    )
    if scheduler:
        scheduler.shutdown(wait=False)
    if oauth_redis is not None:
        await oauth_redis.aclose()
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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        csp_nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = csp_nonce
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        if response.headers.get("content-type", "").startswith("text/html"):
            body_chunks = []
            async for chunk in response.body_iterator:
                body_chunks.append(chunk)
            body = b"".join(body_chunks)
            is_compressed = response.headers.get("content-encoding") == "gzip"
            if is_compressed:
                body = gzip.decompress(body)
            html = body.decode("utf-8")
            html = re.sub(
                r"<script(?![^>]*\bsrc=)([^>]*)>",
                rf'<script nonce="{csp_nonce}"\1>',
                html,
                flags=re.IGNORECASE,
            )
            html = re.sub(
                r"<style([^>]*)>",
                rf'<style nonce="{csp_nonce}"\1>',
                html,
                flags=re.IGNORECASE,
            )
            html = re.sub(
                r"\s(on(?:click|submit|change|keydown))\s*=\s*([\"'])((?:(?!\2).)*)\2",
                lambda match: f' data-csp-{match.group(1)}="{match.group(3)}"',
                html,
                flags=re.IGNORECASE,
            )
            body = html.encode("utf-8")
            if is_compressed:
                body = gzip.compress(body)
            response.headers["content-length"] = str(len(body))

            async def body_chunks():
                yield body

            response.body_iterator = body_chunks()
        storage_origins = ""
        if settings.S3_ENDPOINT:
            parsed_storage_endpoint = urlparse(settings.S3_ENDPOINT)
            if parsed_storage_endpoint.scheme and parsed_storage_endpoint.hostname:
                storage_origin = f"{parsed_storage_endpoint.scheme}://{parsed_storage_endpoint.hostname}"
                storage_origin += (
                    f":{parsed_storage_endpoint.port}"
                    if parsed_storage_endpoint.port
                    else ""
                )
                storage_origins += f" {storage_origin}"
        if not IS_PRODUCTION:
            storage_origins += " http://localhost:9000 http://minio:9000"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{csp_nonce}'; "
            f"style-src 'self' 'nonce-{csp_nonce}'; "
            "font-src 'self'; "
            f"img-src 'self' data: blob:{storage_origins}; "
            f"connect-src 'self'{storage_origins}; "
            "frame-ancestors 'none';"
        )
        return response


def _http_exception_handler(request: Request, exc: HTTPException):
    """Keep internal exception details out of production HTTP responses."""
    if exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        logger.error(
            "HTTP %s while handling %s %s: %s",
            exc.status_code,
            request.method,
            request.url.path,
            exc.detail,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": "Internal server error."},
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


app.add_exception_handler(HTTPException, _http_exception_handler)

# ==============================================================================
# 🛡️ 4. SECURITY & PERFORMANCE MIDDLEWARES
# ==============================================================================

# A. GZip Compression (API Response Payload ကို ချုံ့ပေး၍ Speed မြန်စေသည်)
# Keep HTML rewrite work ahead of gzip so we do not decompress-and-recompress
# every page response just to inject the CSP nonce.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# B. Strict CORS Policy
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in settings.ALLOWED_ORIGINS.split(",")
    if origin.strip()
]

# C. Bank-Grade HTTP Security Headers & Content Security Policy (CSP)

# SecurityHeadersMiddleware is already registered above; keeping the single
# registration avoids duplicate HTML rewriting and redundant header writes.


class CSRFMiddleware(BaseHTTPMiddleware):
    """Protect cookie-authenticated state changes without blocking bearer clients."""

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
    COOKIE_SAMESITE = "lax"

    async def dispatch(self, request: Request, call_next):
        if request.method not in self.SAFE_METHODS:
            has_bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
            csrf_cookie = request.cookies.get("csrf_token")
            csrf_header = request.headers.get("x-csrf-token")
            cookie_authenticated = bool(request.cookies.get("access_token"))
            exempt_login = request.url.path in {
                "/api/v1/auth/login",
                "/api/v1/auth/swagger-login",
            }
            if (
                cookie_authenticated
                and not has_bearer
                and not exempt_login
                and (
                    not csrf_cookie
                    or not csrf_header
                    or not secrets.compare_digest(
                        csrf_cookie, csrf_header
                    )
                )
            ):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "CSRF validation failed."},
                )

        response = await call_next(request)
        if (
            not request.cookies.get("csrf_token")
            and not request.url.path.startswith("/static/")
        ):
            response.set_cookie(
                key="csrf_token",
                value=secrets.token_urlsafe(32),
                httponly=False,
                secure=IS_PRODUCTION or request.url.scheme == "https",
                samesite=self.COOKIE_SAMESITE,
                max_age=60 * 60 * 24,
            )
        return response


app.add_middleware(CSRFMiddleware)

# Register CORS last so it wraps the other middleware and can handle preflight
# requests and attach CORS headers to error responses.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ==============================================================================
# 📂 5. STATIC FILES & STORAGE MOUNTING
# ==============================================================================
class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == status.HTTP_200_OK:
            if path.endswith("service-worker.js"):
                response.headers["Cache-Control"] = "no-cache"
            else:
                response.headers["Cache-Control"] = "public, max-age=3600"
        return response


app.mount("/static", CachedStaticFiles(directory="static"), name="static")

# ==============================================================================
# 🔌 6. BACKEND API ROUTERS (Domain-Driven Design)
# ==============================================================================
app.include_router(auth_router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(auth_profile_router, prefix="/api/v1/auth", tags=["Account Profile"])
app.include_router(account_router, prefix="/api/v1/account", tags=["Account"])
app.include_router(dept_router, prefix="/api/v1/departments", tags=["Departments"])
app.include_router(user_router, prefix="/api/v1/users", tags=["Users"]) 
app.include_router(position_router, prefix=API_V1_PREFIX, tags=["Positions"])
app.include_router(permission_router, prefix=API_V1_PREFIX, tags=["Permissions"])
app.include_router(tasks_router, prefix="/api/v1/tasks", tags=["Tasks"])
app.include_router(kpi_router, prefix=API_V1_KPI_PREFIX, tags=["KPI"])
app.include_router(kpi_period_router, prefix=API_V1_KPI_PREFIX, tags=["KPI Periods"])
app.include_router(kpi_assignment_router, prefix=API_V1_KPI_PREFIX, tags=["KPI Assignments"])
app.include_router(action_plan_router, prefix=API_V1_KPI_PREFIX, tags=["KPI Action Plans"])
app.include_router(upload_router.router, prefix=API_V1_PREFIX, tags=["KPI Excel Uploads"])
app.include_router(targets_router, prefix="/api/v1/targets", tags=["Targets"])
app.include_router(superadmin_dashboard_api_router, prefix="/api/v1/superadmin/dashboard", tags=["Super Admin Dashboard"])
app.include_router(admin_dashboard_api_router, prefix="/api/v1/admin/dashboard", tags=["Admin Dashboard"])
app.include_router(unified_dashboard_api_router, prefix="/api/v1/dashboard", tags=["Unified Dashboard"])
app.include_router(notifications_router, prefix="/api/v1/notifications", tags=["Notifications"])
app.include_router(activity_router, prefix=API_V1_PREFIX)
app.include_router(system_router, prefix=API_V1_PREFIX, tags=["System Settings"])

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
        logger.exception("Database health check failed: %s", e)
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
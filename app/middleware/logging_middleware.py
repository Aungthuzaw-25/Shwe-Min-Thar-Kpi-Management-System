"""
Enterprise Request/Response Logging & Performance Middleware
"""
import time
import uuid
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.models.system import LogLevel
from app.services.system_log_service import record_system_log

logger = logging.getLogger("api.access")

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())
        start_time = time.time()
        
        # Attach request_id to request state for tracing
        request.state.request_id = request_id
        
        client_ip = request.client.host if request.client else "Unknown"
        method = request.method
        path = request.url.path
        
        try:
            response = await call_next(request)
        except Exception as exc:
            await record_system_log(
                LogLevel.ERROR, "HTTP_EXCEPTION", "Unhandled application exception",
                {"request_id": request_id, "method": method, "path": path,
                 "exception_type": type(exc).__name__},
            )
            raise
        
        process_time = (time.time() - start_time) * 1000
        formatted_process_time = f"{process_time:.2f}ms"
        
        # Security Header: Return Correlation ID to Client
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = formatted_process_time
        
        # Skip static assets from noisy access logs
        if not path.startswith("/static") and not path.startswith("/favicon.ico"):
            log_msg = f"[{request_id}] {client_ip} - \"{method} {path}\" {response.status_code} ({formatted_process_time})"
            if response.status_code >= 500:
                logger.error(log_msg)
            elif response.status_code >= 400:
                logger.warning(log_msg)
            else:
                logger.info(log_msg)
            if response.status_code >= 500:
                await record_system_log(
                    LogLevel.ERROR, "HTTP_ERROR", "HTTP request failed",
                    {"request_id": request_id, "method": method, "path": path,
                     "status_code": response.status_code, "duration_ms": round(process_time, 2)},
                )
                
        return response
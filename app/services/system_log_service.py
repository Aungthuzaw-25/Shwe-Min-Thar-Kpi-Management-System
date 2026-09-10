import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from app.database.connection import AsyncSessionLocal
from app.models.system import LogLevel, SystemEventLog

logger = logging.getLogger(__name__)
_SENSITIVE_KEYS = ("password", "token", "secret", "authorization", "cookie", "api_key")


def _safe_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if any(term in str(key).lower() for term in _SENSITIVE_KEYS)
            else _safe_metadata(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_metadata(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


async def record_system_log(
    level: LogLevel,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    safe_details = _safe_metadata(details or {})
    if not safe_details:
        safe_details = {
            "component": event_type.split("_", 1)[0].lower(),
            "event": event_type.lower(),
        }
    getattr(logger, level.value.lower(), logger.info)(message)
    try:
        async with AsyncSessionLocal() as db:
            db.add(SystemEventLog(
                event_type=event_type,
                level=level,
                message=message,
                details=safe_details,
            ))
            await db.commit()
    except Exception:
        logger.exception("Failed to persist system log")

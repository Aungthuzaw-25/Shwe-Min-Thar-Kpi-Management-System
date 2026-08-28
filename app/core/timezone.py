from datetime import datetime, timezone as utc_timezone
from zoneinfo import ZoneInfo

from app.core.config import settings


def get_application_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.TIMEZONE)
    except KeyError as exc:
        raise ValueError(f"Invalid TIMEZONE setting: {settings.TIMEZONE}") from exc


APPLICATION_TIMEZONE = get_application_timezone()


def utc_now_naive() -> datetime:
    """Return UTC without tzinfo for the existing database DateTime columns."""
    return datetime.now(utc_timezone.utc).replace(tzinfo=None)


def as_application_time(value: datetime) -> datetime:
    """Interpret legacy naive timestamps as UTC and display them in app time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=utc_timezone.utc)
    return value.astimezone(APPLICATION_TIMEZONE)

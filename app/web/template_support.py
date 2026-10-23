from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.modules.users.avatar import avatar_color_index, avatar_initials, avatar_url


def _strftime(value: date | datetime | None, format_string: str) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        # Stored timestamps are UTC instants; show them on the panel
        # timezone, the same standard the audit logs use. Formatting the
        # raw UTC value would show a date one day early for
        # evening-UTC timestamps. Plain dates are calendar dates and
        # are never shifted.
        instant = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        value = instant.astimezone(ZoneInfo(settings.TIMEZONE))
    return value.strftime(format_string)


templates = Jinja2Templates(directory="templates")
templates.env.filters["strftime"] = _strftime
templates.env.filters.update(
    avatar_color_index=avatar_color_index,
    avatar_initials=avatar_initials,
    avatar_url=avatar_url,
)
templates.env.globals.update(
    avatar_color_index=avatar_color_index,
    avatar_initials=avatar_initials,
    avatar_url=avatar_url,
    current_user=None,
    user_avatar=None,
    version=settings.VERSION,
    # Company trademark fallback: routes that know the database brand
    # (app.modules.system.branding.resolve_brand) override this per
    # render; the default keeps every other render working unchanged.
    brand={
        "company_name": "Shwe Min Thar",
        "company_tagline": "Performance Matters",
        "logo_url": "/static/image/smt-logo.jpg",
        "has_custom_logo": False,
    },
    oauth_providers={"google": False, "microsoft": False},
)

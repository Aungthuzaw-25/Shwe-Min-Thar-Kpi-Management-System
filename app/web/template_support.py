from datetime import date, datetime

from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.modules.users.avatar import avatar_color_index, avatar_initials, avatar_url


def _strftime(value: date | datetime | None, format_string: str) -> str:
    if value is None:
        return ""
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

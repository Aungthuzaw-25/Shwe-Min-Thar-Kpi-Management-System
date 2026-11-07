import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.modules.users.avatar import avatar_color_index, avatar_initials, avatar_url

logger = logging.getLogger(__name__)


def _asset_paths():
    """Phase 5b bundle spec + manifest locations.

    Defaults resolve from this file (app/web/template_support.py) so they
    work both in the repo (app/web/../../static) and in the API image
    (/code/app/web/../../static = /code/static). Tests override them via
    env vars; clear_asset_caches() resets the cached loads afterwards.
    """
    base = Path(__file__).resolve().parents[2] / "static"
    spec = Path(os.environ.get("ASSET_BUNDLES_SPEC_PATH", base / "css" / "bundles.json"))
    manifest = Path(os.environ.get("ASSET_MANIFEST_PATH", base / "asset-manifest.json"))
    return spec, manifest


_asset_cache = {"spec": None, "manifest": None, "loaded": False}


def clear_asset_caches():
    """Test helper: drop the cached spec/manifest so a test can point the
    env vars at a fresh manifest and re-read it."""
    _asset_cache.update({"spec": None, "manifest": None, "loaded": False})


def _load_assets():
    if _asset_cache["loaded"]:
        return
    _asset_cache["loaded"] = True
    spec_path, manifest_path = _asset_paths()
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        bundles = spec.get("bundles") if isinstance(spec, dict) else None
        _asset_cache["spec"] = bundles if isinstance(bundles, dict) else {}
    except (OSError, ValueError):
        _asset_cache["spec"] = {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        _asset_cache["manifest"] = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        _asset_cache["manifest"] = {}


def asset_bundle(name):
    """Phase 5b asset() helper (API tier mirror of web/src/lib/assets.js).

    Manifest hit -> [hashed href]; manifest miss -> the bundle's original
    hrefs from bundles.json; unknown name -> [] + one warning. Never throws
    into a request and never invents a URL.
    """
    _load_assets()
    hashed = _asset_cache["manifest"].get(name)
    if isinstance(hashed, str) and hashed:
        return [hashed]
    bundle = _asset_cache["spec"].get(name)
    if isinstance(bundle, dict):
        hrefs = bundle.get("hrefs")
        if isinstance(hrefs, list) and hrefs:
            return list(hrefs)
    logger.warning("asset(): unknown bundle %r", name)
    return []


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
        "logo_url": "/static/image/smt-logo.webp",
        "has_custom_logo": False,
    },
    oauth_providers={"google": False, "microsoft": False},
    # Phase 5b asset bundles: default ON; set ASSET_BUNDLES=false to serve
    # the original individual links. The hashed bundles are built by
    # tools/build_asset_bundles.mjs (both Docker images run it at build
    # time); if the manifest is missing the helper falls back to the
    # individual links, so local dev without a build is unaffected.
    # Read from the environment directly (as the scheduler's
    # ENABLE_BATCH_NOTIFICATIONS is) rather than adding a Settings field:
    # the flag is a deployment-time render switch, not app configuration.
    asset=asset_bundle,
    asset_bundles=os.environ.get("ASSET_BUNDLES", "true").lower() != "false",
)

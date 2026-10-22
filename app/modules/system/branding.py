"""Company trademark (branding) support for System Settings.

Storage layout — no parallel settings system:

- The company name stays in the existing ``general`` system setting
  (``{"company_name": ..., "timezone": ..., "date_format": ...}``), which
  is where the settings page has always saved it.
- The logo reference lives in a ``branding`` system setting
  (``{"logo_storage": ..., "logo_object": ..., "logo_updated_at": ...}``)
  that only the branding endpoints in ``app.modules.system.router`` may
  write; generic settings saves for that key are refused there.

Logo bytes follow the project's existing upload patterns:

- Validation and normalization mirror the profile-avatar pipeline
  (``app.modules.auth.profile_router.normalize_avatar_image``): the
  content must really decode as JPEG/PNG/WebP via Pillow, size and
  dimension/pixel limits apply, and the image is re-encoded to WebP
  before storage, which strips metadata and any embedded payload.
  The original filename and the client-supplied content type are never
  trusted or used.
- Storage mirrors the evidence pipeline (``app.services.storage_service``):
  when MinIO/S3 is configured the logo goes to the bucket under the fixed
  key ``branding/logo.webp``; a new upload overwrites that same key, so
  the new logo replaces the old one everywhere it is served from. When
  object storage is unavailable the logo falls back to local disk under
  ``uploads/branding/``, the same pattern profile avatars use.
"""

import logging
import time
from io import BytesIO
from pathlib import Path

from fastapi.concurrency import run_in_threadpool
from PIL import Image, ImageOps
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.system import repo
from app.modules.system.schemas import (
    validate_company_name,
    validate_company_tagline,
)
from app.services.storage_service import storage_service

logger = logging.getLogger(__name__)

GENERAL_SETTINGS_KEY = "general"
BRANDING_SETTINGS_KEY = "branding"
BRANDING_SETTING_DESCRIPTION = "Company trademark (logo) reference"

DEFAULT_COMPANY_NAME = "Shwe Min Thar"
DEFAULT_COMPANY_TAGLINE = "Performance Matters"
DEFAULT_LOGO_URL = "/static/image/smt-logo.jpg"
CUSTOM_LOGO_URL = "/api/v1/system/branding/logo"

LOGO_OBJECT_KEY = "branding/logo.webp"
LOGO_FILENAME = "logo.webp"
LOGO_MEDIA_TYPE = "image/webp"
MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_LOGO_DIMENSION = 4096
MAX_LOGO_PIXELS = MAX_LOGO_DIMENSION * MAX_LOGO_DIMENSION
MAX_LOGO_SIZE = (1024, 1024)
LOGO_FORMATS = {"JPEG", "PNG", "WEBP"}


def branding_directory() -> Path:
    return Path("uploads/branding").resolve()


def _local_logo_path() -> Path:
    return branding_directory() / LOGO_FILENAME


def normalize_logo_image(content: bytes) -> bytes:
    """Validate a real image and re-encode it as WebP (avatar pattern).

    Unlike avatars, a logo is not cropped to a square: its aspect ratio
    is preserved and it is only shrunk to fit ``MAX_LOGO_SIZE``.
    """
    if not content or len(content) > MAX_LOGO_BYTES:
        raise ValueError("Logo image exceeds the allowed size.")

    try:
        with Image.open(BytesIO(content)) as image:
            if image.format not in LOGO_FORMATS:
                raise ValueError("Unsupported logo image format.")
            if (
                image.width > MAX_LOGO_DIMENSION
                or image.height > MAX_LOGO_DIMENSION
                or image.width * image.height > MAX_LOGO_PIXELS
            ):
                raise ValueError("Logo image dimensions are too large.")
            image.verify()
        with Image.open(BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image)
            mode = (
                "RGBA"
                if "transparency" in image.info or image.mode in {"RGBA", "LA", "PA"}
                else "RGB"
            )
            image = image.convert(mode)
            image.thumbnail(MAX_LOGO_SIZE, Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="WEBP", quality=90, method=6)
            return output.getvalue()
    except (
        OSError,
        Image.DecompressionBombError,
        SyntaxError,
        EOFError,
    ) as exc:
        raise ValueError("Invalid image data.") from exc


def _s3_available() -> bool:
    return bool(
        getattr(storage_service, "is_available", False)
        and getattr(storage_service, "s3_client", None)
    )


async def store_logo(
    content: bytes, *, previous_updated_at: int | None = None
) -> dict:
    """Store normalized logo bytes; returns the settings reference.

    The version timestamp strictly increases on every upload, so the
    logo URL (``?v=<version>``) always changes when the logo is replaced
    and browsers never keep showing the old logo from cache.
    """
    updated_at = max(int(time.time()), (previous_updated_at or 0) + 1)
    if _s3_available():
        await run_in_threadpool(
            storage_service.s3_client.put_object,
            Bucket=storage_service.bucket_name,
            Key=LOGO_OBJECT_KEY,
            Body=content,
            ContentType=LOGO_MEDIA_TYPE,
        )
        return {
            "logo_storage": "s3",
            "logo_object": LOGO_OBJECT_KEY,
            "logo_updated_at": updated_at,
        }
    path = _local_logo_path()
    await run_in_threadpool(path.parent.mkdir, parents=True, exist_ok=True)
    await run_in_threadpool(path.write_bytes, content)
    return {
        "logo_storage": "local",
        "logo_object": LOGO_FILENAME,
        "logo_updated_at": updated_at,
    }


async def load_logo(reference: dict | None) -> bytes | None:
    """Read the custom logo bytes for the reference, or ``None``.

    The object location is fixed (``LOGO_OBJECT_KEY`` / ``LOGO_FILENAME``);
    nothing caller-controlled is ever used as a path or key.
    """
    if not reference or not reference.get("logo_object"):
        return None
    if reference.get("logo_storage") == "s3" and _s3_available():
        try:
            response = await run_in_threadpool(
                storage_service.s3_client.get_object,
                Bucket=storage_service.bucket_name,
                Key=LOGO_OBJECT_KEY,
            )
            return await run_in_threadpool(response["Body"].read)
        except Exception:
            logger.exception("Stored company logo could not be read from S3.")
            return None
    path = _local_logo_path()
    if not path.is_file():
        return None
    return await run_in_threadpool(path.read_bytes)


async def delete_logo(reference: dict | None) -> None:
    """Best-effort removal of the stored logo bytes."""
    if not reference or not reference.get("logo_object"):
        return
    if reference.get("logo_storage") == "s3":
        try:
            await storage_service.delete_file(LOGO_OBJECT_KEY)
        except Exception:
            logger.exception("Stored company logo could not be deleted from S3.")
        return
    path = _local_logo_path()
    await run_in_threadpool(path.unlink, missing_ok=True)


async def read_settings_values(db: AsyncSession) -> dict[str, dict]:
    """All settings values by lower-cased key (settings count is small)."""
    values: dict[str, dict] = {}
    for row in await repo.get_all_settings(db):
        value = getattr(row, "value", None)
        values[str(row.key).lower()] = value if isinstance(value, dict) else {}
    return values


def brand_from_values(values: dict[str, dict]) -> dict:
    """Build the public brand shape from raw settings values."""
    general = values.get(GENERAL_SETTINGS_KEY, {})
    try:
        company_name = validate_company_name(general.get("company_name"))
    except ValueError:
        company_name = DEFAULT_COMPANY_NAME
    if "company_tagline" in general:
        try:
            # A saved tagline wins even when empty ("" = no subtitle);
            # an invalid saved value falls back to the default.
            company_tagline = validate_company_tagline(
                general.get("company_tagline")
            )
        except ValueError:
            company_tagline = DEFAULT_COMPANY_TAGLINE
    else:
        company_tagline = DEFAULT_COMPANY_TAGLINE
    branding = values.get(BRANDING_SETTINGS_KEY, {})
    has_custom_logo = bool(branding.get("logo_object"))
    updated_at = branding.get("logo_updated_at")
    if not isinstance(updated_at, int) or updated_at <= 0:
        updated_at = None
    if has_custom_logo:
        logo_url = (
            f"{CUSTOM_LOGO_URL}?v={updated_at}"
            if updated_at
            else CUSTOM_LOGO_URL
        )
    else:
        logo_url = DEFAULT_LOGO_URL
    return {
        "company_name": company_name,
        "company_tagline": company_tagline,
        "logo_url": logo_url,
        "has_custom_logo": has_custom_logo,
        "logo_updated_at": updated_at if has_custom_logo else None,
    }


async def resolve_brand(db: AsyncSession) -> dict:
    """Brand for display; defaults when nothing was ever saved.

    Never raises on a broken settings read — a settings problem must not
    break the login page, which is this value's main consumer.
    """
    try:
        values = await read_settings_values(db)
    except Exception:
        logger.exception("Company trademark settings could not be read.")
        values = {}
    return brand_from_values(values)

"""Company trademark settings: company name + logo (Backend stage 1).

Covered here:
- The trademark value saves/reads through the existing settings store:
  the name goes into the ``general`` setting, the logo reference into a
  ``branding`` setting that generic settings writes cannot forge.
- Logo upload validation follows the avatar pipeline: non-images and
  oversize files are rejected, real images are re-encoded to WebP.
- Upload/reset require the settings-edit permission.
- The public brand read returns name + logo + provider flags and
  nothing sensitive; provider flags mirror the OAuth configuration.
- Defaults: "Shwe Min Thar" + the committed SMT logo asset.
"""

import time
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image
from pydantic import ValidationError

from app.core.config import settings as app_settings
from app.core.exceptions import PermissionDeniedException
from app.models.system import SystemSetting
from app.modules.system import branding
from app.modules.system import router as system_router
from app.modules.system.schemas import (
    CompanyTrademarkUpdate,
    normalize_general_settings,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class FakeSettingsDB:
    """In-memory stand-in for the system_settings table."""

    def __init__(self, rows=()):
        self.rows = {row.key: row for row in rows}
        self.added = []

    async def execute(self, stmt):
        params = {}
        try:
            params = stmt.compile().params
        except Exception:
            params = {}
        keys = [value for value in params.values() if isinstance(value, str)]
        if len(keys) == 1:
            row = self.rows.get(keys[0])
            return FakeResult([row] if row is not None else [])
        return FakeResult(list(self.rows.values()))

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, SystemSetting):
            self.rows[obj.key] = obj

    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None

    async def rollback(self):
        return None


def _user(role_name="Super Admin"):
    return SimpleNamespace(
        id=uuid4(),
        role=SimpleNamespace(name=role_name, permissions=[]),
        permission_overrides=[],
    )


def _setting(key, value):
    return SystemSetting(key=key, value=value, description=None)


def _image_bytes(fmt="PNG", size=(64, 32), color=(180, 30, 30)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def _upload(content, filename="logo.png"):
    return UploadFile(filename=filename, file=BytesIO(content))


@pytest.fixture()
def local_logo_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        branding, "branding_directory", lambda: tmp_path / "branding"
    )
    monkeypatch.setattr(branding.storage_service, "is_available", False)
    return tmp_path / "branding"


# ---------------------------------------------------------------------------
# Defaults + brand resolution
# ---------------------------------------------------------------------------

def test_brand_defaults_when_nothing_saved():
    brand = branding.brand_from_values({})
    assert brand == {
        "company_name": "Shwe Min Thar",
        "company_tagline": "Performance Matters",
        "logo_url": "/static/image/smt-logo.jpg",
        "has_custom_logo": False,
        "logo_updated_at": None,
    }


def test_brand_uses_saved_name_and_custom_logo():
    brand = branding.brand_from_values(
        {
            "general": {
                "company_name": "  Acme Corp  ",
                "company_tagline": "  Grow Together  ",
            },
            "branding": {"logo_object": "branding/logo.webp", "logo_updated_at": 42},
        }
    )
    assert brand["company_name"] == "Acme Corp"
    assert brand["company_tagline"] == "Grow Together"
    assert brand["has_custom_logo"] is True
    assert brand["logo_url"] == "/api/v1/system/branding/logo?v=42"


def test_brand_tagline_saved_empty_means_no_subtitle():
    brand = branding.brand_from_values({"general": {"company_tagline": ""}})
    assert brand["company_tagline"] == ""


def test_brand_tagline_invalid_saved_value_falls_back_to_default():
    brand = branding.brand_from_values(
        {"general": {"company_tagline": "<b>bad</b>"}}
    )
    assert brand["company_tagline"] == "Performance Matters"


def test_brand_falls_back_to_default_for_invalid_saved_name():
    brand = branding.brand_from_values({"general": {"company_name": "<b>x</b>"}})
    assert brand["company_name"] == "Shwe Min Thar"


# ---------------------------------------------------------------------------
# Company name save/read through the settings endpoints
# ---------------------------------------------------------------------------

async def test_update_branding_saves_name_into_general_and_preserves_fields():
    db = FakeSettingsDB(
        [_setting("general", {"company_name": "Old", "timezone": "Asia/Yangon"})]
    )
    result = await system_router.update_branding(
        CompanyTrademarkUpdate(company_name="New Co"), db, _user()
    )
    assert result["company_name"] == "New Co"
    assert db.rows["general"].value == {
        "company_name": "New Co",
        "timezone": "Asia/Yangon",
    }


async def test_update_branding_creates_general_row_when_missing():
    db = FakeSettingsDB()
    result = await system_router.update_branding(
        CompanyTrademarkUpdate(company_name="First Co"), db, _user()
    )
    assert result["company_name"] == "First Co"
    assert db.rows["general"].value["company_name"] == "First Co"


async def test_update_branding_requires_edit_permission():
    with pytest.raises(PermissionDeniedException):
        await system_router.update_branding(
            CompanyTrademarkUpdate(company_name="Nope"),
            FakeSettingsDB(),
            _user("Employee"),
        )


@pytest.mark.parametrize(
    "bad_name", ["", "   ", "<script>", "line\nbreak", "x" * 101]
)
def test_company_name_validation_rejects_bad_values(bad_name):
    with pytest.raises((ValidationError, ValueError)):
        CompanyTrademarkUpdate(company_name=bad_name)


# ---------------------------------------------------------------------------
# Tagline save/read + length limits (name <= 60, tagline <= 90)
# ---------------------------------------------------------------------------

async def test_update_branding_saves_tagline_with_name():
    db = FakeSettingsDB()
    result = await system_router.update_branding(
        CompanyTrademarkUpdate(
            company_name="Tagged Co", company_tagline="  Quality First  "
        ),
        db,
        _user(),
    )
    assert result["company_tagline"] == "Quality First"
    assert db.rows["general"].value["company_tagline"] == "Quality First"


async def test_update_branding_without_tagline_keeps_saved_tagline():
    db = FakeSettingsDB(
        [_setting("general", {"company_name": "Old", "company_tagline": "Keep Me"})]
    )
    result = await system_router.update_branding(
        CompanyTrademarkUpdate(company_name="Renamed"), db, _user()
    )
    assert result["company_tagline"] == "Keep Me"


async def test_update_branding_empty_tagline_clears_subtitle():
    db = FakeSettingsDB(
        [_setting("general", {"company_name": "Old", "company_tagline": "Old Line"})]
    )
    result = await system_router.update_branding(
        CompanyTrademarkUpdate(company_name="Old", company_tagline=""), db, _user()
    )
    assert result["company_tagline"] == ""


def test_company_name_length_limits():
    # At the limit: accepted. Over the limit: rejected with a clear error.
    assert CompanyTrademarkUpdate(company_name="n" * 60).company_name == "n" * 60
    with pytest.raises(ValidationError):
        CompanyTrademarkUpdate(company_name="n" * 61)


def test_company_tagline_length_limits():
    update = CompanyTrademarkUpdate(company_name="Co", company_tagline="t" * 90)
    assert update.company_tagline == "t" * 90
    with pytest.raises(ValidationError):
        CompanyTrademarkUpdate(company_name="Co", company_tagline="t" * 91)


def test_general_settings_save_enforces_length_limits():
    assert normalize_general_settings({"company_name": "n" * 60}) == {
        "company_name": "n" * 60
    }
    with pytest.raises(ValueError):
        normalize_general_settings({"company_name": "n" * 61})
    with pytest.raises(ValueError):
        normalize_general_settings({"company_tagline": "t" * 91})
    with pytest.raises(ValueError):
        normalize_general_settings({"company_tagline": "bad\nline"})


def test_general_settings_save_validates_name_and_strips_logo_fields():
    value = normalize_general_settings(
        {
            "company_name": "  Padded Name ",
            "timezone": "UTC",
            "logo_url": "https://evil.example/logo.png",
            "logo_object": "branding/logo.webp",
        }
    )
    assert value == {"company_name": "Padded Name", "timezone": "UTC"}
    with pytest.raises(ValueError):
        normalize_general_settings({"company_name": "<b>bad</b>"})


async def test_generic_setting_write_cannot_forge_branding_row():
    payload = SimpleNamespace(
        key="branding", value={"logo_object": "branding/logo.webp"}
    )
    with pytest.raises(HTTPException) as exc_info:
        await system_router.create_new_setting(payload, FakeSettingsDB(), _user())
    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# Logo image validation (avatar pipeline rules)
# ---------------------------------------------------------------------------

def test_normalize_logo_rejects_non_image():
    with pytest.raises(ValueError):
        branding.normalize_logo_image(b"this is not an image")


def test_normalize_logo_rejects_oversize():
    with pytest.raises(ValueError):
        branding.normalize_logo_image(b"x" * (branding.MAX_LOGO_BYTES + 1))


def test_normalize_logo_rejects_svg():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with pytest.raises(ValueError):
        branding.normalize_logo_image(svg)


def test_normalize_logo_reencodes_to_webp_and_keeps_aspect_ratio():
    normalized = branding.normalize_logo_image(_image_bytes(size=(200, 50)))
    with Image.open(BytesIO(normalized)) as image:
        assert image.format == "WEBP"
        assert image.size == (200, 50)


# ---------------------------------------------------------------------------
# Logo upload / serve / reset endpoints
# ---------------------------------------------------------------------------

async def test_upload_logo_stores_reference_and_serves_bytes(local_logo_dir):
    db = FakeSettingsDB()
    result = await system_router.upload_branding_logo(
        _upload(_image_bytes()), db, _user()
    )
    assert result["has_custom_logo"] is True
    assert result["logo_url"].startswith("/api/v1/system/branding/logo?v=")
    reference = db.rows["branding"].value
    assert reference["logo_storage"] == "local"
    assert reference["logo_object"] == "logo.webp"

    response = await system_router.read_branding_logo(db)
    assert response.media_type == "image/webp"
    with Image.open(BytesIO(response.body)) as image:
        assert image.format == "WEBP"


async def test_upload_logo_replaces_previous_logo(local_logo_dir):
    db = FakeSettingsDB()
    user = _user()
    await system_router.upload_branding_logo(
        _upload(_image_bytes(color=(255, 0, 0))), db, user
    )
    first_url = (await system_router.read_branding(db, user))["logo_url"]
    await system_router.upload_branding_logo(
        _upload(_image_bytes(color=(0, 0, 255))), db, user
    )
    brand = await system_router.read_branding(db, user)
    assert brand["logo_url"] != first_url  # version bumped, caches refresh
    stored = (local_logo_dir / "logo.webp").read_bytes()
    with Image.open(BytesIO(stored)) as image:
        assert image.convert("RGB").getpixel((5, 5)) == (0, 0, 255)


async def test_upload_logo_rejects_bad_type(local_logo_dir):
    with pytest.raises(HTTPException) as exc_info:
        await system_router.upload_branding_logo(
            _upload(b"plain text file", filename="logo.txt"),
            FakeSettingsDB(),
            _user(),
        )
    assert exc_info.value.status_code == 400


async def test_upload_logo_rejects_oversize(local_logo_dir):
    big_upload = _upload(b"x" * (branding.MAX_LOGO_BYTES + 1))
    big_upload.size = branding.MAX_LOGO_BYTES + 1
    with pytest.raises(HTTPException) as exc_info:
        await system_router.upload_branding_logo(
            big_upload, FakeSettingsDB(), _user()
        )
    assert exc_info.value.status_code == 413


async def test_upload_logo_requires_edit_permission(local_logo_dir):
    with pytest.raises(PermissionDeniedException):
        await system_router.upload_branding_logo(
            _upload(_image_bytes()), FakeSettingsDB(), _user("Employee")
        )


async def test_reset_logo_returns_to_default(local_logo_dir):
    db = FakeSettingsDB()
    user = _user()
    await system_router.upload_branding_logo(_upload(_image_bytes()), db, user)
    result = await system_router.reset_branding_logo(db, user)
    assert result["has_custom_logo"] is False
    assert result["logo_url"] == "/static/image/smt-logo.jpg"
    assert not (local_logo_dir / "logo.webp").exists()
    with pytest.raises(HTTPException) as exc_info:
        await system_router.read_branding_logo(db)
    assert exc_info.value.status_code == 404


async def test_logo_endpoint_404_without_custom_logo(local_logo_dir):
    with pytest.raises(HTTPException) as exc_info:
        await system_router.read_branding_logo(FakeSettingsDB())
    assert exc_info.value.status_code == 404


async def test_logo_storage_uses_minio_when_available(monkeypatch):
    class FakeBody:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

    class FakeS3Client:
        def __init__(self):
            self.objects = {}

        def put_object(self, Bucket, Key, Body, ContentType):
            self.objects[(Bucket, Key)] = (Body, ContentType)

        def get_object(self, Bucket, Key):
            return {"Body": FakeBody(self.objects[(Bucket, Key)][0])}

    fake_client = FakeS3Client()
    fake_storage = SimpleNamespace(
        is_available=True, s3_client=fake_client, bucket_name="kpi-evidence"
    )
    monkeypatch.setattr(branding, "storage_service", fake_storage)

    reference = await branding.store_logo(b"webp-bytes")
    assert reference["logo_storage"] == "s3"
    assert reference["logo_object"] == "branding/logo.webp"
    assert fake_client.objects[("kpi-evidence", "branding/logo.webp")] == (
        b"webp-bytes",
        "image/webp",
    )
    assert await branding.load_logo(reference) == b"webp-bytes"


# ---------------------------------------------------------------------------
# Public brand read (login page): minimal + provider truth
# ---------------------------------------------------------------------------

async def test_public_brand_returns_only_safe_fields(monkeypatch):
    monkeypatch.setattr(app_settings, "OAUTH_ENABLED", False)
    monkeypatch.setattr(app_settings, "GOOGLE_OIDC_ENABLED", False)
    monkeypatch.setattr(app_settings, "MICROSOFT_OIDC_ENABLED", False)
    db = FakeSettingsDB(
        [
            _setting("general", {"company_name": "Public Co", "timezone": "UTC"}),
            _setting("email", {"providers": {"resend": {"from_email": "a@b.c"}}}),
            _setting("security", {"session_timeout": 30}),
        ]
    )
    result = await system_router.read_public_branding(db)
    assert set(result) == {
        "company_name",
        "company_tagline",
        "logo_url",
        "has_custom_logo",
        "logo_updated_at",
        "oauth_providers",
    }
    assert result["company_name"] == "Public Co"
    assert result["oauth_providers"] == {"google": False, "microsoft": False}
    assert "a@b.c" not in str(result)
    assert "session_timeout" not in str(result)


async def test_public_brand_provider_flags_reflect_configuration(monkeypatch):
    monkeypatch.setattr(app_settings, "OAUTH_ENABLED", True)
    monkeypatch.setattr(app_settings, "GOOGLE_OIDC_ENABLED", True)
    monkeypatch.setattr(app_settings, "GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setattr(app_settings, "GOOGLE_CLIENT_SECRET", "google-secret")
    monkeypatch.setattr(app_settings, "GOOGLE_REDIRECT_URI", "https://x.example/cb")
    monkeypatch.setattr(app_settings, "APP_BASE_URL", "https://x.example")
    monkeypatch.setattr(app_settings, "MICROSOFT_OIDC_ENABLED", False)
    result = await system_router.read_public_branding(FakeSettingsDB())
    assert result["oauth_providers"] == {"google": True, "microsoft": False}
    # The flags must never leak the configuration itself.
    assert "google-secret" not in str(result)
    assert "google-client" not in str(result)


async def test_public_brand_survives_broken_settings_store():
    class BrokenDB:
        async def execute(self, _stmt):
            raise RuntimeError("storage unavailable")

    result = await system_router.read_public_branding(BrokenDB())
    assert result["company_name"] == "Shwe Min Thar"
    assert result["logo_url"] == "/static/image/smt-logo.jpg"


def test_public_brand_endpoints_over_http_without_login(local_logo_dir):
    from fastapi.testclient import TestClient

    from app.database.connection import get_db
    from app.main import app

    db = FakeSettingsDB([_setting("general", {"company_name": "HTTP Co"})])
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/system/branding/public")
            assert response.status_code == 200
            body = response.json()
            assert body["company_name"] == "HTTP Co"
            assert body["company_tagline"] == "Performance Matters"
            assert body["logo_url"] == "/static/image/smt-logo.jpg"
            assert set(body["oauth_providers"]) == {"google", "microsoft"}

            # No custom logo yet: the logo endpoint itself is a clean 404.
            assert client.get("/api/v1/system/branding/logo").status_code == 404

            # The settings-side read stays behind authentication.
            assert client.get("/api/v1/system/branding").status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_public_brand_timestamp_is_recent_when_logo_uploaded():
    # The version query value comes from the stored upload time, so a new
    # logo changes the URL and browsers fetch the new image.
    now = int(time.time())
    brand = branding.brand_from_values(
        {"branding": {"logo_object": "branding/logo.webp", "logo_updated_at": now}}
    )
    assert brand["logo_url"] == f"/api/v1/system/branding/logo?v={now}"

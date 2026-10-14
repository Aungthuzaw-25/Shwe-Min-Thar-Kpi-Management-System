import asyncio
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from app.modules.auth.profile_router import (
    _avatar_initials,
    account_router,
    apply_profile_patch,
    get_account,
    normalize_avatar_image,
    reauthenticate_account,
)
from app.modules.auth.profile_router import router as profile_router
from app.modules.auth.profile_schemas import (
    AccountProfilePatch,
    AccountReauthentication,
    ProfilePatch,
)
from app.modules.auth.router import oauth_provider_availability


def _image_bytes(image_format="PNG", size=(32, 24)):
    source = BytesIO()
    Image.new("RGB", size, color=(120, 50, 20)).save(source, format=image_format)
    return source.getvalue()


def test_profile_patch_accepts_only_supported_fields_and_validates_values():
    profile = ProfilePatch(first_name="Aung", phone_number="+95 9 1234 5678", bio="Hello")

    assert profile.model_dump(exclude_unset=True) == {
        "first_name": "Aung",
        "phone_number": "+95 9 1234 5678",
        "bio": "Hello",
    }
    with pytest.raises(ValidationError):
        ProfilePatch(role_id="unexpected")
    with pytest.raises(ValidationError):
        ProfilePatch(phone_number="phone<script>")
    with pytest.raises(ValidationError):
        ProfilePatch(first_name=42)


def test_profile_patch_can_clear_optional_fields_but_not_names():
    assert ProfilePatch(region_town=None, bio=None).model_dump(exclude_unset=True) == {
        "region_town": None,
        "bio": None,
    }
    with pytest.raises(ValidationError):
        ProfilePatch(first_name="   ")


def test_avatar_content_is_decoded_and_normalized_to_webp():
    normalized = normalize_avatar_image(_image_bytes())

    with Image.open(BytesIO(normalized)) as image:
        assert image.format == "WEBP"
        assert image.size == (256, 256)


def test_avatar_decoder_rejects_unsupported_or_oversized_images():
    with pytest.raises(ValueError):
        normalize_avatar_image(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    with pytest.raises(ValueError):
        normalize_avatar_image(_image_bytes(size=(4097, 1)))


def test_avatar_initials_use_names_and_grapheme_clusters():
    assert _avatar_initials("Superadmin QA") == "SQ"
    assert _avatar_initials("Aung Kyaw Min") == "AM"
    assert _avatar_initials("Kyaw") == "K"
    assert _avatar_initials("  Aung   Kyaw ") == "AK"
    assert _avatar_initials("kyaw min") == "KM"
    assert _avatar_initials("", "example@example.com") == "E"
    assert _avatar_initials("") == "?"
    assert _avatar_initials("\u1000\u1031\u1001") == "\u1000\u1031"


@pytest.mark.asyncio
async def test_profile_patch_updates_only_submitted_fields_and_audits_field_names():
    user = SimpleNamespace(
        id=uuid4(),
        first_name="Aung",
        last_name="Zaw",
        region_town=None,
        phone_number=None,
        bio=None,
    )

    class Session:
        def __init__(self):
            self.entries = []
            self.commits = 0

        def add(self, entry):
            self.entries.append(entry)

        async def commit(self):
            self.commits += 1

        async def refresh(self, _user):
            return None

    db = Session()
    changed = await apply_profile_patch(
        db,
        user,
        ProfilePatch(phone_number="+95 9 1234 5678", bio="A short bio"),
        "127.0.0.1",
    )

    assert changed == ["bio", "phone_number"]
    assert user.first_name == "Aung"
    assert db.commits == 1
    assert db.entries[0].action == "PROFILE_UPDATED"
    assert db.entries[0].new_value == {"fields": ["bio", "phone_number"]}


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class _AccountDB:
    def __init__(self, user, accounts):
        self.user = user
        self.accounts = accounts
        self.calls = 0

    async def execute(self, _statement):
        self.calls += 1
        return _Result([self.user] if self.calls == 1 else self.accounts)


@pytest.mark.asyncio
async def test_account_me_includes_expected_profile_and_security_metadata():
    from datetime import datetime
    from types import SimpleNamespace
    from uuid import uuid4

    changed_at = datetime(2026, 1, 2, 3, 4)
    user = SimpleNamespace(
        id=uuid4(),
        first_name="Aung",
        last_name="Zaw",
        email="aung@example.com",
        phone_number="+95 9 123456",
        role=SimpleNamespace(name="Super Admin"),
        position=SimpleNamespace(name="Director"),
        user_departments=[
            SimpleNamespace(
                is_deleted=False,
                department=SimpleNamespace(name="Operations"),
            )
        ],
        avatar_path=None,
        password_changed_at=changed_at,
        local_password_enabled=True,
    )
    db = _AccountDB(user, [
        SimpleNamespace(provider="google", provider_email="aung@gmail.com", linked_at=changed_at)
    ])

    result = await get_account(db, user)

    assert result["display_name"] == "Aung Zaw"
    assert result["email"] == "aung@example.com"
    assert result["phone"] == "+95 9 123456"
    assert result["role"] == "Super Admin"
    assert result["department"] == "Operations"
    assert result["position"] == "Director"
    assert result["avatar_url"] is None
    assert result["initials"] == "AZ"
    assert result["password_changed_at"] == changed_at
    assert result["linked_providers"]["google"]["email"] == "aung@gmail.com"
    assert set(result["configured_providers"]) == {"google", "microsoft"}
    assert result["local_password_enabled"] is True
    assert result["is_super_admin"] is True
    assert result["show_connected_accounts"] is True
    assert result["features_available"]["connected_account_management"] is True


def test_oauth_provider_status_contains_only_configured_booleans(monkeypatch):
    from app.modules.auth import router as auth_router

    monkeypatch.setattr(auth_router.settings, "OAUTH_ENABLED", True)
    monkeypatch.setattr(auth_router.settings, "GOOGLE_OIDC_ENABLED", True)
    monkeypatch.setattr(auth_router.settings, "GOOGLE_CLIENT_ID", "placeholder-id")
    monkeypatch.setattr(auth_router.settings, "GOOGLE_CLIENT_SECRET", "placeholder-secret")
    monkeypatch.setattr(auth_router.settings, "GOOGLE_REDIRECT_URI", "https://example.test/google")
    monkeypatch.setattr(auth_router.settings, "MICROSOFT_OIDC_ENABLED", False)

    result = oauth_provider_availability()

    assert result == {"google": True, "microsoft": False}
    assert all(type(enabled) is bool for enabled in result.values())


def test_oauth_provider_uses_app_base_url_and_common_microsoft_tenant(monkeypatch):
    from app.modules.auth import router as auth_router

    monkeypatch.setattr(auth_router.settings, "OAUTH_ENABLED", True)
    monkeypatch.setattr(auth_router.settings, "APP_BASE_URL", "http://127.0.0.1:8000/")
    monkeypatch.setattr(auth_router.settings, "GOOGLE_OIDC_ENABLED", True)
    monkeypatch.setattr(auth_router.settings, "GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setattr(auth_router.settings, "GOOGLE_CLIENT_SECRET", "google-secret")
    monkeypatch.setattr(auth_router.settings, "GOOGLE_REDIRECT_URI", None)
    monkeypatch.setattr(auth_router.settings, "MICROSOFT_OIDC_ENABLED", True)
    monkeypatch.setattr(auth_router.settings, "MICROSOFT_CLIENT_ID", "microsoft-client")
    monkeypatch.setattr(auth_router.settings, "MICROSOFT_CLIENT_SECRET", "microsoft-secret")
    monkeypatch.setattr(auth_router.settings, "MICROSOFT_REDIRECT_URI", None)
    monkeypatch.setattr(auth_router.settings, "MICROSOFT_TENANT_ID", None)
    monkeypatch.setattr(auth_router.settings, "MICROSOFT_TENANT", "common")

    google = auth_router._provider("google")
    microsoft = auth_router._provider("microsoft")

    assert google.redirect_uri == "http://127.0.0.1:8000/api/v1/auth/google/callback"
    assert microsoft.redirect_uri == "http://127.0.0.1:8000/api/v1/auth/microsoft/callback"
    assert "login.microsoftonline.com/common/" in microsoft.discovery_url


def test_account_profile_patch_accepts_only_frontend_display_name_and_phone():
    patch = AccountProfilePatch(display_name="Aung Thu Zaw", phone="+95 9 123456")

    assert patch.model_dump() == {
        "display_name": "Aung Thu Zaw",
        "phone": "+95 9 123456",
    }
    with pytest.raises(ValidationError):
        AccountProfilePatch(display_name="Aung", role="Super Admin")
    assert AccountProfilePatch(display_name="  Aung   Thu Zaw  ").display_name == "Aung Thu Zaw"
    with pytest.raises(ValidationError):
        AccountProfilePatch(display_name="Invalid\tName")
    with pytest.raises(ValidationError):
        AccountProfilePatch(display_name="Aung", phone="12345")


def test_account_api_registers_requested_endpoint_paths():
    routes = set()
    for route in (*profile_router.routes, *account_router.routes):
        if hasattr(route, "methods"):
            routes.update((route.path, method) for method in route.methods)

    assert ("/me", "GET") in routes
    assert ("/me", "PATCH") in routes
    assert ("/me/profile", "GET") in routes
    assert ("/me/profile", "PATCH") in routes
    assert ("/me/avatar", "POST") in routes
    assert ("/me/avatar", "DELETE") in routes
    assert ("/avatars/{filename}", "GET") in routes
    assert ("/me/password", "POST") in routes
    assert ("/me/reauth", "POST") in routes


@pytest.mark.asyncio
async def test_local_password_reauthentication_marks_only_current_session(monkeypatch):
    from starlette.requests import Request

    calls = []

    class Result:
        rowcount = 1

    class DB:
        committed = False

        async def execute(self, statement):
            calls.append(statement)
            return Result()

        async def commit(self):
            self.committed = True

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/account/me/reauth",
        "headers": [(b"authorization", b"Bearer token")],
        "query_string": b"",
        "scheme": "https",
        "server": ("example.test", 443),
        "client": ("127.0.0.1", 1234),
    })
    user_id, session_id = uuid4(), uuid4()
    user = SimpleNamespace(
        id=user_id,
        local_password_enabled=True,
        hashed_password="encoded-password",
    )
    monkeypatch.setattr(
        "app.modules.auth.profile_router.security.verify_password",
        lambda password, hashed: password == "current-password" and hashed == "encoded-password",
    )
    monkeypatch.setattr(
        "app.modules.auth.profile_router.security.decode_token",
        lambda *_args, **_kwargs: {"sid": str(session_id), "user_id": str(user_id)},
    )
    monkeypatch.setattr(
        "app.modules.auth.profile_router.run_in_threadpool",
        lambda function, *args: asyncio.sleep(0, result=function(*args)),
    )
    db = DB()

    result = await reauthenticate_account(
        request,
        AccountReauthentication(current_password="current-password"),
        user,
        db,
    )

    assert result["status"] == "reauthenticated"
    assert result["valid_for_seconds"] == 600
    assert db.committed
    assert session_id in calls[0].compile().params.values()
    assert "last_reauthenticated_at" in str(calls[0])


@pytest.mark.asyncio
async def test_passwordless_reauthentication_must_use_provider_flow():
    from fastapi import HTTPException
    from starlette.requests import Request

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/account/me/reauth",
        "headers": [],
        "query_string": b"",
        "scheme": "https",
        "server": ("example.test", 443),
        "client": ("127.0.0.1", 1234),
    })

    with pytest.raises(HTTPException) as error:
        await reauthenticate_account(
            request,
            AccountReauthentication(current_password="anything"),
            SimpleNamespace(id=uuid4(), local_password_enabled=False, hashed_password=None),
            object(),
        )

    assert error.value.status_code == 400
    assert "OAuth provider" in error.value.detail
    assert any(not hasattr(route, "methods") for route in account_router.routes)

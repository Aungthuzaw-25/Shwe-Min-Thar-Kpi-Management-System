from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.password_policy import get_password_policy, validate_password_strength
from app.modules.system.router import (
    _require_super_admin_email_settings,
    _sanitize_setting_value,
    read_all_settings,
    update_existing_setting,
)
from app.modules.system.schemas import (
    EmailConfigurationValue,
    EmailProviderLifecycleRequest,
    EmailProviderSaveRequest,
    PasswordPolicyValue,
    SystemSettingResponse,
    SystemSettingUpdate,
)


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value


class FakeDB:
    def __init__(self, setting):
        self.setting = setting

    async def execute(self, _statement):
        return FakeResult(self.setting)


class FakeAuditDB:
    def __init__(self):
        self.audit_entries = []

    def add(self, entry):
        self.audit_entries.append(entry)

    async def commit(self):
        return None


def test_security_setting_is_normalized_for_existing_json_storage():
    value = _sanitize_setting_value(
        "security",
        {
            "min_length": 10,
            "require_complexity": True,
            "password_expiry": True,
            "session_timeout": 120,
        },
    )

    assert value["minimum_password_length"] == 10
    assert value["min_length"] == 10
    assert value["force_password_change_days"] == 90
    assert value["password_expiry"] is True


def test_new_setting_response_allows_null_updated_at():
    response = SystemSettingResponse.model_validate(
        {
            "id": uuid4(),
            "key": "security",
            "value": {"minimum_password_length": 10},
            "description": None,
            "created_at": datetime.now(UTC),
            "updated_at": None,
        }
    )
    assert response.updated_at is None


def test_security_setting_rejects_invalid_types_and_ranges():
    with pytest.raises(ValidationError):
        PasswordPolicyValue(
            minimum_password_length="10",  # noqa: S106 - synthetic test fixture; not a production credential
            require_complexity=True,
            force_password_change_days=90,
        )
    with pytest.raises(HTTPException):
        _sanitize_setting_value(
            "security",
            {
                "minimum_password_length": 4,
                "require_complexity": True,
                "force_password_change_days": 90,
            },
        )


@pytest.mark.parametrize(
    "email",
    ["support@example.com", " admin@example.com "],
)
def test_email_configuration_trims_and_accepts_valid_addresses(email):
    value = EmailConfigurationValue(support_email=email)
    assert value.support_email == email.strip()


@pytest.mark.parametrize("email", ["support@", "support", "@example.com"])
def test_email_configuration_rejects_invalid_support_addresses(email):
    with pytest.raises(ValidationError):
        EmailConfigurationValue(support_email=email)


@pytest.mark.parametrize("field", ["from_email", "support_email"])
def test_email_configuration_rejects_invalid_addresses_for_both_fields(field):
    with pytest.raises(ValidationError):
        EmailConfigurationValue(**{field: "invalid-address"})


def test_email_configuration_allows_clearing_support_email():
    value = _sanitize_setting_value(
        "email",
        {
            "email_provider": "resend",
            "from_email": "noreply@example.com",
            "from_name": "KPI System",
            "support_email": "   ",
        },
    )
    config = value["providers"]["resend"]
    assert config["support_email"] is None
    assert config["from_email"] == "noreply@example.com"


def test_smtp_provider_settings_are_validated_and_preserved_without_password():
    value = _sanitize_setting_value(
        "email",
        {
            "email_provider": "SMTP",
            "from_email": "noreply@example.com",
            "from_name": "KPI System",
            "support_email": None,
            "smtp_host": " smtp.example.com ",
            "smtp_port": 465,
            "smtp_username": " relay-user ",
            "smtp_encryption": "SSL",
        },
    )
    config = value["providers"]["smtp"]
    assert value["active_provider"] is None
    assert config["smtp_host"] == "smtp.example.com"
    assert config["smtp_port"] == 465
    assert config["smtp_username"] == "relay-user"
    assert config["smtp_encryption"] == "ssl"
    assert "smtp_password" not in str(value)


def test_legacy_email_setting_is_kept_as_disabled_provider_for_explicit_reactivation():
    value = _sanitize_setting_value(
        "email",
        {
            "email_provider": "resend",
            "from_email": "onboarding@resend.dev",
            "from_name": "KPI System",
            "support_email": "support@example.com",
        },
    )
    assert value["active_provider"] is None
    assert value["providers"]["resend"]["enabled"] is False
    assert value["providers"]["resend"]["from_email"] == "onboarding@resend.dev"
    assert "smtp_host" not in value["providers"]["resend"]


def test_email_configuration_rejects_invalid_smtp_port_and_encryption():
    with pytest.raises(HTTPException):
        _sanitize_setting_value(
            "email",
            {
                "email_provider": "smtp",
                "from_email": "noreply@example.com",
                "smtp_port": 70000,
            },
        )
    with pytest.raises(HTTPException):
        _sanitize_setting_value(
            "email",
            {
                "email_provider": "smtp",
                "from_email": "noreply@example.com",
                "smtp_encryption": "custom",
            },
        )


def test_email_configuration_rejects_non_resend_provider_and_html_name():
    with pytest.raises(HTTPException):
        _sanitize_setting_value("email", {"email_provider": "unsupported"})
    with pytest.raises(HTTPException):
        _sanitize_setting_value("email", {"from_name": "<script>alert(1)</script>"})


def test_provider_save_schema_does_not_accept_runtime_secret_fields():
    payload = EmailProviderSaveRequest.model_validate(
        {
            "provider": "resend",
            "configuration": {
                "from_email": "noreply@example.com",
                "resend_api_key": "mock-only-secret",
                "smtp_password": "mock-only-password",
            },
        }
    )
    serialized = payload.model_dump_json()
    assert "mock-only-secret" not in serialized
    assert "mock-only-password" not in serialized


def test_only_super_admin_can_manage_email_settings():
    _require_super_admin_email_settings(
        SimpleNamespace(role=SimpleNamespace(name="Super Admin"))
    )
    with pytest.raises(HTTPException) as exc:
        _require_super_admin_email_settings(
            SimpleNamespace(role=SimpleNamespace(name="Department User"))
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_email_setting_api_update_persists_support_email_and_audits_values(monkeypatch):
    from app.modules.system import router as system_router

    setting = SimpleNamespace(
        id=uuid4(),
        key="email",
        value={
            "email_provider": "resend",
            "from_email": "noreply@example.com",
            "from_name": "KPI System",
            "support_email": "old@example.com",
        },
        description="Email delivery",
        created_at=datetime.now(UTC),
        updated_at=None,
    )
    db = FakeAuditDB()

    async def find_setting(_db, _key):
        return setting

    async def update_setting(_db, _setting, payload):
        _setting.value = payload.value
        return _setting

    monkeypatch.setattr(system_router.repo, "get_setting_by_key", find_setting)
    monkeypatch.setattr(system_router.repo, "update_setting", update_setting)
    monkeypatch.setattr(system_router.settings, "RESEND_API_KEY", "fake-only-test-key")
    current_user = SimpleNamespace(
        id=uuid4(),
        role=SimpleNamespace(name="Super Admin"),
        permission_overrides=[],
    )
    current_user.role.permissions = []

    await update_existing_setting(
        "email",
        SystemSettingUpdate(
            value={
                "email_provider": "resend",
                "from_email": "noreply@example.com",
                "from_name": "KPI System",
                "support_email": "new@example.com",
            }
        ),
        db,
        current_user,
    )

    assert setting.value["providers"]["resend"]["support_email"] == "new@example.com"
    assert setting.value["providers"]["resend"]["from_email"] == "noreply@example.com"
    assert (
        db.audit_entries[0].old_value["value"]["providers"]["resend"]["support_email"]
        == "old@example.com"
    )
    assert (
        db.audit_entries[0].new_value["value"]["providers"]["resend"]["support_email"]
        == "new@example.com"
    )


@pytest.mark.asyncio
async def test_email_settings_api_reports_key_status_without_exposing_secret(monkeypatch):
    from app.modules.system import router as system_router

    setting = SimpleNamespace(
        id=uuid4(),
        key="email",
        value={"support_email": "support@example.com"},
        description=None,
        created_at=datetime.now(UTC),
        updated_at=None,
    )

    async def get_settings(_db):
        return [setting]

    monkeypatch.setattr(system_router.repo, "get_all_settings", get_settings)
    monkeypatch.setattr(system_router.settings, "RESEND_API_KEY", "fake-only-test-key")
    current_user = SimpleNamespace(
        role=SimpleNamespace(name="Super Admin", permissions=[]),
        permission_overrides=[],
    )

    result = await read_all_settings(FakeAuditDB(), current_user)

    response = SystemSettingResponse.model_validate(result[0])
    assert response.value["providers"]["resend"]["support_email"] == "support@example.com"
    assert "RESEND_API_KEY" not in str(response.model_dump())
    assert "fake-only-test-key" not in str(response.model_dump())


@pytest.mark.asyncio
async def test_provider_registry_reports_credential_status_without_returning_secrets(monkeypatch):
    from app.modules.system import router as system_router

    setting = SimpleNamespace(
        id=uuid4(),
        key="email",
        value={
            "providers": {
                "resend": {
                    "enabled": False,
                    "from_email": "noreply@example.com",
                    "from_name": "KPI System",
                    "support_email": None,
                    "tested": False,
                    "last_test_status": None,
                }
            },
            "active_provider": None,
        },
    )
    monkeypatch.setattr(
        system_router.repo, "get_setting_by_key", lambda *_args: _return(setting)
    )
    monkeypatch.setattr(
        system_router.settings, "RESEND_API_KEY", "mock-only-provider-secret"
    )
    user = SimpleNamespace(
        role=SimpleNamespace(name="Super Admin"),
        permission_overrides=[],
    )
    result = await system_router.list_email_providers(FakeAuditDB(), user)
    assert result["configured_providers"][0]["credential_configured"] is True
    assert "mock-only-provider-secret" not in str(result)


@pytest.mark.asyncio
async def test_saving_provider_stores_disabled_draft_and_clears_previous_active(monkeypatch):
    from app.modules.system import router as system_router

    setting = SimpleNamespace(
        id=uuid4(),
        key="email",
        value={
            "providers": {
                "resend": {
                    "enabled": True,
                    "from_email": "noreply@example.com",
                    "from_name": "KPI System",
                    "support_email": None,
                    "tested": True,
                    "last_test_status": "ready",
                }
            },
            "active_provider": "resend",
        },
        description="Email delivery",
        created_at=datetime.now(UTC),
        updated_at=None,
    )
    db = FakeAuditDB()
    monkeypatch.setattr(system_router.repo, "get_setting_by_key", lambda *_args: _return(setting))

    async def update_setting(_db, _setting, payload):
        _setting.value = payload.value
        return _setting

    monkeypatch.setattr(system_router.repo, "update_setting", update_setting)
    user = SimpleNamespace(
        id=uuid4(),
        role=SimpleNamespace(name="Super Admin"),
        permission_overrides=[],
    )

    result = await system_router.save_email_provider(
        "resend",
        EmailProviderSaveRequest(
            provider="resend",
            configuration=EmailConfigurationValue(
                from_email="noreply@example.com",
                from_name="KPI System",
            ),
        ),
        db,
        user,
    )
    assert result["enabled"] is False
    assert setting.value["active_provider"] is None
    assert setting.value["providers"]["resend"]["enabled"] is False


@pytest.mark.asyncio
async def test_provider_test_does_not_activate_and_enable_then_disable_clears_active(monkeypatch):
    from app.modules.system import router as system_router

    setting = SimpleNamespace(
        id=uuid4(),
        key="email",
        value={
            "providers": {
                "smtp": {
                    "enabled": False,
                    "from_email": "mailer@example.com",
                    "from_name": "KPI System",
                    "support_email": None,
                    "smtp_host": "smtp.example.com",
                    "smtp_port": 587,
                    "smtp_username": None,
                    "smtp_encryption": "starttls",
                    "tested": False,
                    "last_test_status": None,
                }
            },
            "active_provider": None,
        },
        description="Email delivery",
        created_at=datetime.now(UTC),
        updated_at=None,
    )
    db = FakeAuditDB()
    monkeypatch.setattr(
        system_router.repo, "get_setting_by_key", lambda *_args: _return(setting)
    )

    async def update_setting(_db, _setting, payload):
        _setting.value = payload.value
        return _setting

    async def test_configuration(_provider, _configuration):
        return "Ready"

    monkeypatch.setattr(system_router.repo, "update_setting", update_setting)
    monkeypatch.setattr(
        system_router.email_service, "test_configuration", test_configuration
    )
    monkeypatch.setattr(
        system_router.email_service,
        "validate_provider_configuration",
        lambda *_args: None,
    )
    user = SimpleNamespace(
        id=uuid4(),
        role=SimpleNamespace(name="Super Admin"),
        permission_overrides=[],
    )

    tested = await system_router.test_configured_email_provider(
        "smtp", db, user
    )
    assert tested["success"] is True
    assert setting.value["active_provider"] is None
    assert setting.value["providers"]["smtp"]["tested"] is True
    enabled = await system_router.update_email_provider_lifecycle(
        "smtp", EmailProviderLifecycleRequest(action="enable"), db, user
    )
    assert enabled["active_provider"] == "smtp"
    assert setting.value["providers"]["smtp"]["enabled"] is True
    await system_router.update_email_provider_lifecycle(
        "smtp", EmailProviderLifecycleRequest(action="disable"), db, user
    )
    assert setting.value["active_provider"] is None
    assert setting.value["providers"]["smtp"]["enabled"] is False


@pytest.mark.asyncio
async def test_removed_provider_can_be_added_again_without_duplicate_entries(monkeypatch):
    from app.modules.system import router as system_router

    setting = SimpleNamespace(
        id=uuid4(),
        key="email",
        value={
            "providers": {
                "resend": {
                    "enabled": True,
                    "from_email": "noreply@example.com",
                    "from_name": "KPI System",
                    "support_email": None,
                    "tested": True,
                    "last_test_status": "ready",
                }
            },
            "active_provider": "resend",
        },
        description="Email delivery",
        created_at=datetime.now(UTC),
        updated_at=None,
    )
    db = FakeAuditDB()
    monkeypatch.setattr(
        system_router.repo, "get_setting_by_key", lambda *_args: _return(setting)
    )

    async def update_setting(_db, _setting, payload):
        _setting.value = payload.value
        return _setting

    monkeypatch.setattr(system_router.repo, "update_setting", update_setting)
    user = SimpleNamespace(
        id=uuid4(),
        role=SimpleNamespace(name="Super Admin"),
        permission_overrides=[],
    )

    await system_router.remove_email_provider("resend", db, user)
    assert setting.value["providers"] == {}
    assert setting.value["active_provider"] is None
    await system_router.save_email_provider(
        "resend",
        EmailProviderSaveRequest(
            provider="resend",
            configuration=EmailConfigurationValue(
                from_email="noreply@example.com",
                from_name="KPI System",
            ),
        ),
        db,
        user,
    )
    assert list(setting.value["providers"]) == ["resend"]
    assert setting.value["providers"]["resend"]["enabled"] is False


async def _return(value):
    return value


@pytest.mark.asyncio
async def test_saved_policy_is_used_by_password_validation():
    setting = SimpleNamespace(
        value={
            "minimum_password_length": 10,
            "require_complexity": False,
            "force_password_change_days": 90,
        }
    )
    policy = await get_password_policy(FakeDB(setting))
    assert policy == {"minimum_length": 10, "require_complexity": False}

    with pytest.raises(HTTPException):
        validate_password_strength("short", **policy)
    assert validate_password_strength("long-enough", **policy) is True

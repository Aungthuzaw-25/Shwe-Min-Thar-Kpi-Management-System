import smtplib
import ssl

import httpx
import pytest

from app.background_jobs import notification_jobs
from app.modules.system.router import _safe_backup_value, _sanitize_setting_value
from app.modules.system.schemas import SystemSettingResponse
from app.services import email_service
from app.services.email_service import (
    EmailAttachment,
    EmailAuthenticationError,
    EmailConfiguration,
    EmailConfigurationError,
    EmailDisabledError,
    EmailMessage,
    EmailProviderFactory,
    EmailProviderResolver,
    EmailSecrets,
    ResendProvider,
    SMTPProvider,
    _support_footer,
)
from app.services.email_service import (
    email_service as service,
)


def config(
    provider="resend",
    *,
    from_email="noreply@example.com",
    support_email="support@example.com",
    smtp_host="smtp.example.com",
    smtp_port=587,
    smtp_username="mailer",
    smtp_encryption="starttls",
):
    return EmailConfiguration(
        email_provider=provider,
        from_email=from_email,
        from_name="KPI System",
        support_email=support_email,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_encryption=smtp_encryption,
    )


def test_support_footer_preserves_message_contract_and_adds_safe_content():
    original = EmailMessage(
        to=["person@example.com"],
        subject="Subject",
        text_body="Body",
        html_body="<p>Body</p>",
        cc=["copy@example.com"],
        attachments=[EmailAttachment("report.txt", b"report", "text/plain")],
    )

    updated = _support_footer(
        original,
        config(support_email="help&support@example.com"),
    )

    assert isinstance(updated, EmailMessage)
    assert updated.to == original.to
    assert updated.subject == original.subject
    assert updated.cc == original.cc
    assert updated.attachments == original.attachments
    assert updated.text_body.startswith("Body\n\n")
    assert "help&support@example.com" in updated.text_body
    assert "&amp;" in updated.html_body
    assert updated.reply_to == "help&support@example.com"


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self.payload = payload if payload is not None else {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.resend.com/domains")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("request failed", request=request, response=response)

    def json(self):
        return self.payload


class FakeAsyncClient:
    def __init__(self, *, get_response=None, post_response=None, exception=None, **_kwargs):
        self.get_response = get_response or FakeResponse(
            {"data": [{"name": "example.com", "status": "verified"}]}
        )
        self.post_response = post_response or FakeResponse({"id": "mock-email-id"})
        self.exception = exception
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        if self.exception:
            raise self.exception
        return self.get_response

    async def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        if self.exception:
            raise self.exception
        return self.post_response


class FakeSMTP:
    instances = []
    login_error = None

    def __init__(self, host, port, **kwargs):
        self.host = host
        self.port = port
        self.kwargs = kwargs
        self.events = []
        self.sent_message = None
        self.sent_to = None
        self.__class__.instances.append(self)

    def ehlo(self):
        self.events.append("ehlo")

    def starttls(self, **kwargs):
        self.starttls_kwargs = kwargs
        self.events.append("starttls")

    def login(self, username, password):
        self.events.append(("login", username, password))
        if self.login_error:
            raise self.login_error

    def send_message(self, message, *, to_addrs):
        self.sent_message = message
        self.sent_to = to_addrs
        self.events.append("send")

    def quit(self):
        self.events.append("quit")

    def close(self):
        self.events.append("close")


def _mock_settings(monkeypatch, configuration, secrets=None):
    if secrets is None:
        secrets = EmailSecrets("fake-resend-key", None)

    provider_value = {
        key: getattr(configuration, key)
        for key in (
            "from_email",
            "from_name",
            "support_email",
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_encryption",
        )
    }
    provider_value.update(enabled=True, tested=True, last_test_status="ready")

    async def load_settings():
        return {
            "providers": {configuration.email_provider: provider_value},
            "active_provider": configuration.email_provider,
        }

    monkeypatch.setattr(email_service, "_load_email_settings", load_settings)
    monkeypatch.setattr(email_service, "_load_email_secrets", lambda: secrets)


def test_provider_factory_registers_only_implemented_providers():
    assert set(EmailProviderFactory.available_providers()) == {"resend", "smtp"}
    assert isinstance(EmailProviderFactory.create("resend"), ResendProvider)
    assert isinstance(EmailProviderFactory.create("smtp"), SMTPProvider)


def test_resolver_selects_only_enabled_provider_from_multiple_configurations():
    state = {
        "active_provider": "smtp",
        "providers": {
            "resend": {
                "enabled": False,
                "from_email": "resend@example.com",
                "from_name": "KPI System",
                "support_email": None,
            },
            "smtp": {
                "enabled": True,
                "from_email": "smtp@example.com",
                "from_name": "KPI System",
                "support_email": None,
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "smtp_username": None,
                "smtp_encryption": "starttls",
                "tested": True,
            },
        },
    }
    selected = EmailProviderResolver.resolve_value(state)
    assert selected.email_provider == "smtp"

    state["active_provider"] = None
    state["providers"]["smtp"]["enabled"] = False
    with pytest.raises(EmailDisabledError):
        EmailProviderResolver.resolve_value(state)


@pytest.mark.asyncio
async def test_no_active_provider_returns_disabled_without_instantiating_provider(monkeypatch):
    async def no_active_provider():
        return {
            "providers": {
                "resend": {
                    "enabled": False,
                    "from_email": "noreply@example.com",
                    "from_name": "KPI System",
                    "support_email": None,
                }
            },
            "active_provider": None,
        }

    monkeypatch.setattr(email_service, "_load_email_settings", no_active_provider)
    monkeypatch.setattr(
        EmailProviderFactory,
        "create",
        classmethod(lambda cls, _name: pytest.fail("Disabled provider was instantiated")),
    )
    assert not await service.send(
        EmailMessage(to=["person@example.com"], subject="Subject", text_body="Body")
    )

@pytest.mark.asyncio
async def test_provider_neutral_notification_sends_same_message_through_resend(monkeypatch):
    _mock_settings(monkeypatch, config())
    client = FakeAsyncClient()
    monkeypatch.setattr(
        email_service.httpx, "AsyncClient", lambda **kwargs: client
    )

    sent = await notification_jobs.send_email_notification(
        "person@example.com", "Notification", "A notification"
    )

    assert sent is True
    method, url, request = client.requests[0]
    assert method == "POST"
    assert url.endswith("/emails")
    assert request["headers"]["Authorization"] == "Bearer fake-resend-key"
    assert request["json"]["from"] == "KPI System <noreply@example.com>"
    assert request["json"]["to"] == ["person@example.com"]
    assert request["json"]["reply_to"] == "support@example.com"
    assert "Need help? Contact support@example.com" in request["json"]["text"]


@pytest.mark.asyncio
async def test_provider_neutral_notification_sends_same_message_through_smtp(monkeypatch):
    monkeypatch.setattr(FakeSMTP, "instances", [])
    monkeypatch.setattr(FakeSMTP, "login_error", None)
    smtp_configuration = config("smtp")
    _mock_settings(
        monkeypatch,
        smtp_configuration,
        EmailSecrets(resend_api_key=None, smtp_password="smtp-test-password"),  # noqa: S106 - mock-only credential
    )
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)

    sent = await notification_jobs.send_email_notification(
        "person@example.com", "Notification", "A notification"
    )

    smtp = FakeSMTP.instances[-1]
    assert sent is True
    assert smtp.host == "smtp.example.com"
    assert smtp.events[:3] == ["ehlo", "starttls", "ehlo"]
    starttls_context = smtp.starttls_kwargs["context"]
    assert isinstance(starttls_context, ssl.SSLContext)
    assert starttls_context.verify_mode == ssl.CERT_REQUIRED
    assert starttls_context.check_hostname is True
    assert ("login", "mailer", "smtp-test-password") in smtp.events
    assert smtp.sent_message["From"] == "KPI System <noreply@example.com>"
    assert smtp.sent_message["Reply-To"] == "support@example.com"
    assert "Need help? Contact support@example.com" in smtp.sent_message.get_body(
        preferencelist=("plain",)
    ).get_content()
    assert smtp.sent_to == ["person@example.com"]


@pytest.mark.asyncio
async def test_smtp_without_authentication_does_not_require_a_password(monkeypatch):
    monkeypatch.setattr(FakeSMTP, "instances", [])
    configuration = config("smtp", smtp_username=None)
    _mock_settings(
        monkeypatch,
        configuration,
        EmailSecrets(resend_api_key=None, smtp_password=None),
    )
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)

    assert await service.send(
        EmailMessage(to=["person@example.com"], subject="Subject", text_body="Body")
    )
    assert not any(
        isinstance(event, tuple) and event[0] == "login"
        for event in FakeSMTP.instances[-1].events
    )


@pytest.mark.asyncio
async def test_missing_provider_secrets_and_sender_fail_safely(monkeypatch):
    _mock_settings(monkeypatch, config(), EmailSecrets(None, None))
    assert not await service.send(
        EmailMessage(to=["person@example.com"], subject="Subject", text_body="Body")
    )

    _mock_settings(
        monkeypatch,
        config(from_email=None),
        EmailSecrets("fake-resend-key", None),
    )
    assert not await service.send(
        EmailMessage(to=["person@example.com"], subject="Subject", text_body="Body")
    )


def test_smtp_configuration_validation_reports_missing_host_and_invalid_port():
    provider = SMTPProvider()
    secrets = EmailSecrets(None, None)
    missing_host = config("smtp", smtp_host=None)
    invalid_port = config("smtp", smtp_port=70000)

    with pytest.raises(EmailConfigurationError, match="SMTP Host"):
        provider.validate_configuration(missing_host, secrets)
    with pytest.raises(EmailConfigurationError, match="SMTP Port"):
        provider.validate_configuration(invalid_port, secrets)


def test_smtp_authentication_failure_is_normalized(monkeypatch):
    monkeypatch.setattr(FakeSMTP, "instances", [])
    monkeypatch.setattr(
        FakeSMTP,
        "login_error",
        smtplib.SMTPAuthenticationError(535, b"credentials rejected"),
    )
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    provider = SMTPProvider()
    configuration = config("smtp")
    secrets = EmailSecrets(resend_api_key=None, smtp_password="mock-password")  # noqa: S106 - mock-only credential

    with pytest.raises(EmailAuthenticationError, match="SMTP authentication failed"):
        provider._connect(configuration, secrets)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [httpx.TimeoutException("timeout"), httpx.ConnectError("network unavailable")],
)
async def test_resend_connection_and_delivery_failures_are_normalized(monkeypatch, error):
    _mock_settings(monkeypatch, config())
    client = FakeAsyncClient(exception=error)
    monkeypatch.setattr(email_service.httpx, "AsyncClient", lambda **kwargs: client)

    assert not await service.send(
        EmailMessage(to=["person@example.com"], subject="Subject", text_body="Body")
    )
    configuration = config()
    secrets = EmailSecrets("fake-resend-key", None)
    provider = ResendProvider()

    with pytest.raises(email_service.EmailConnectionError):
        await provider.test_connection(configuration, secrets)


@pytest.mark.asyncio
async def test_resend_test_connection_verifies_sender_without_sending(monkeypatch):
    client = FakeAsyncClient()
    monkeypatch.setattr(
        email_service.httpx, "AsyncClient", lambda **kwargs: client
    )
    result = await ResendProvider().test_connection(
        config(), EmailSecrets("fake-resend-key", None)
    )
    assert result == "Ready"
    assert len(client.requests) == 1
    assert client.requests[0][0] == "GET"


@pytest.mark.asyncio
async def test_resend_test_mode_is_supported_without_verified_custom_domain(monkeypatch):
    client = FakeAsyncClient(get_response=FakeResponse({"data": []}))
    monkeypatch.setattr(
        email_service.httpx, "AsyncClient", lambda **kwargs: client
    )
    result = await ResendProvider().test_connection(
        config(from_email="onboarding@resend.dev"),
        EmailSecrets("fake-resend-key", None),
    )
    assert result == "Ready — Resend Test Mode"


@pytest.mark.asyncio
async def test_missing_from_email_has_specific_diagnostic_before_missing_api_key():
    configuration = config(from_email=None)
    secrets = EmailSecrets(None, None)
    provider = ResendProvider()

    with pytest.raises(EmailConfigurationError, match="From Email is required"):
        await provider.test_connection(configuration, secrets)


@pytest.mark.asyncio
async def test_smtp_test_connection_uses_current_provider_and_never_sends(monkeypatch):
    monkeypatch.setattr(FakeSMTP, "instances", [])
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    _mock_settings(
        monkeypatch,
        config("smtp"),
        EmailSecrets(None, "smtp-test-password"),
    )

    assert await service.test_connection() == "Ready"
    assert "send" not in FakeSMTP.instances[-1].events


@pytest.mark.asyncio
async def test_resend_test_connection_uses_selected_provider_through_service(monkeypatch):
    _mock_settings(monkeypatch, config("resend"))
    client = FakeAsyncClient()
    monkeypatch.setattr(email_service.httpx, "AsyncClient", lambda **kwargs: client)

    assert await service.test_connection() == "Ready"
    assert client.requests[0][0] == "GET"


@pytest.mark.asyncio
async def test_smtp_ssl_connection_uses_ssl_adapter_and_none_disables_tls(monkeypatch):
    monkeypatch.setattr(FakeSMTP, "instances", [])
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    provider = SMTPProvider()
    secrets = EmailSecrets(None, None)

    assert await provider.test_connection(
        config("smtp", smtp_username=None, smtp_encryption="ssl"),
        secrets,
    ) == "Ready"
    ssl_smtp = FakeSMTP.instances[-1]
    assert "context" in ssl_smtp.kwargs
    ssl_context = ssl_smtp.kwargs["context"]
    assert isinstance(ssl_context, ssl.SSLContext)
    assert ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert ssl_context.check_hostname is True
    assert "starttls" not in ssl_smtp.events

    assert await provider.test_connection(
        config("smtp", smtp_username=None, smtp_encryption="none"),
        secrets,
    ) == "Ready"
    plain_smtp = FakeSMTP.instances[-1]
    assert "starttls" not in plain_smtp.events


@pytest.mark.asyncio
async def test_support_footer_is_omitted_when_not_configured(monkeypatch):
    _mock_settings(
        monkeypatch,
        config(support_email=None),
        EmailSecrets("fake-resend-key", None),
    )
    client = FakeAsyncClient()
    monkeypatch.setattr(email_service.httpx, "AsyncClient", lambda **kwargs: client)

    await service.send(
        EmailMessage(to=["person@example.com"], subject="Subject", text_body="Body")
    )

    assert client.requests[0][2]["json"]["text"] == "Body"
    assert "reply_to" not in client.requests[0][2]["json"]


@pytest.mark.asyncio
async def test_provider_failure_logs_do_not_expose_credentials(monkeypatch, caplog):
    _mock_settings(
        monkeypatch,
        config(),
        EmailSecrets("mock-only-resend-secret", "mock-only-smtp-secret"),
    )
    client = FakeAsyncClient(post_response=FakeResponse(status_code=503))
    monkeypatch.setattr(email_service.httpx, "AsyncClient", lambda **kwargs: client)

    assert not await service.send(
        EmailMessage(to=["person@example.com"], subject="Subject", text_body="Body")
    )
    assert "mock-only-resend-secret" not in caplog.text
    assert "mock-only-smtp-secret" not in caplog.text


def test_system_setting_api_filters_provider_secrets_and_old_smtp_settings():
    response = SystemSettingResponse.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "key": "email",
            "value": {
                "email_provider": "smtp",
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "smtp_username": "mailer",
                "smtp_password": "not-a-real-password",
                "resend_api_key": "not-a-real-api-key",
                "smtp_password_configured": True,
            },
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    assert response.value["smtp_host"] == "smtp.example.com"
    assert response.value["smtp_password_configured"] is True
    assert "smtp_password" not in response.value
    assert "resend_api_key" not in response.value
    assert "not-a-real-password" not in str(response.model_dump())
    assert "not-a-real-api-key" not in str(response.model_dump())


def test_system_setting_update_rejects_provider_secrets_and_backup_redacts_legacy_values():
    from fastapi import HTTPException

    setting = {"SMTP_PASSWORD": "mock-only-secret"}
    with pytest.raises(HTTPException):
        _sanitize_setting_value("email", setting)
    backup = _safe_backup_value(
        "email",
        {
            "from_email": "noreply@example.com",
            "smtp_password": "mock-only-secret",
            "nested": {"resend_api_key": "mock-only-key"},
        },
    )
    assert backup["smtp_password"] == "[REDACTED]"  # noqa: S105 - redaction marker
    assert backup["nested"]["resend_api_key"] == "[REDACTED]"
    assert "mock-only-secret" not in str(backup)
    assert "mock-only-key" not in str(backup)

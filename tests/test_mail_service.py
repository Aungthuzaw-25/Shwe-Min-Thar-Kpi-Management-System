import httpx
import pytest

from app.background_jobs import notification_jobs
from app.modules.system.router import _sanitize_setting_value
from app.modules.system.schemas import SystemSettingResponse
from app.services import email_service
from app.services.email_service import EmailConfiguration


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self.payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.resend.com/domains")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("request failed", request=request, response=response)

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, *, get_response=None, post_response=None, exception=None, **_kwargs):
        self.get_response = get_response or FakeResponse(
            {"data": [{"name": "example.com", "status": "verified"}]}
        )
        self.post_response = post_response or FakeResponse({"id": "email-id"})
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


def test_system_settings_email_sanitizer_rejects_smtp_fields_and_credentials():
    value = _sanitize_setting_value(
        "email",
        {
            "host": "smtp.example.com",
            "username": "mailer@example.com",
            "password": "not-a-real-password",
            "from_email": "noreply@example.com",
            "support_email": "support@example.com",
        },
    )
    assert value == {
        "email_provider": "resend",
        "from_email": "noreply@example.com",
        "from_name": "Shwe Min Thar KPI Management System",
        "support_email": "support@example.com",
    }


def test_system_settings_reject_api_keys_instead_of_persisting_them():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _sanitize_setting_value(
            "general", {"nested": {"RESEND_API_KEY": "not-a-real-key"}}
        )
    assert exc.value.status_code == 422


def test_system_setting_response_never_exposes_email_credentials():
    response = SystemSettingResponse.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "key": "email",
            "value": {
                "host": "smtp.example.com",
                "password": "not-a-real-password",
                "api_key": "not-a-real-key",
                "from_email": "noreply@example.com",
                "resend_api_key_configured": True,
            },
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    assert response.value == {
        "from_email": "noreply@example.com",
        "resend_api_key_configured": True,
    }


def test_support_footer_is_omitted_when_support_email_is_not_configured():
    assert email_service._email_text_with_support(
        "Message", None, "KPI System"
    ) == "Message"


@pytest.mark.asyncio
async def test_notification_delivery_uses_resend_and_dynamic_support_email(monkeypatch):
    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", "mock-resend-key")
    monkeypatch.setattr(
        email_service,
        "_load_email_configuration",
        _resolved(
            EmailConfiguration(
                from_email="noreply@example.com",
                from_name="KPI System",
                support_email="help@example.com",
            )
        ),
    )
    client = FakeClient()
    monkeypatch.setattr(email_service.httpx, "AsyncClient", lambda **kwargs: client)

    sent = await notification_jobs.send_email_notification(
        "user@example.com", "Notification", "A notification"
    )

    assert sent is True
    method, url, request = client.requests[0]
    assert method == "POST"
    assert url == "https://api.resend.com/emails"
    assert request["headers"]["Authorization"] == "Bearer mock-resend-key"
    assert request["json"]["from"] == "KPI System <noreply@example.com>"
    assert request["json"]["to"] == ["user@example.com"]
    assert "help@example.com" in request["json"]["text"]


@pytest.mark.asyncio
async def test_email_is_skipped_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", None)
    client = FakeClient()
    monkeypatch.setattr(email_service.httpx, "AsyncClient", lambda **kwargs: client)

    assert await email_service.send_resend_email(
        "user@example.com", "Subject", "Body"
    ) is False
    assert not client.requests


@pytest.mark.asyncio
async def test_email_is_skipped_when_sender_missing(monkeypatch):
    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", "mock-resend-key")
    monkeypatch.setattr(
        email_service,
        "_load_email_configuration",
        _resolved(
            EmailConfiguration(
                from_email=None,
                from_name="KPI System",
                support_email=None,
            )
        ),
    )
    client = FakeClient()
    monkeypatch.setattr(email_service.httpx, "AsyncClient", lambda **kwargs: client)

    assert await email_service.send_resend_email(
        "user@example.com", "Subject", "Body"
    ) is False
    assert not client.requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.TimeoutException("timeout"),
        httpx.ConnectError("network unavailable"),
    ],
)
async def test_email_delivery_network_failures_are_reported_without_raising(monkeypatch, error):
    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", "mock-resend-key")
    monkeypatch.setattr(
        email_service,
        "_load_email_configuration",
        _resolved(
            EmailConfiguration(
                from_email="noreply@example.com",
                from_name="KPI System",
                support_email=None,
            )
        ),
    )
    client = FakeClient(exception=error)
    monkeypatch.setattr(email_service.httpx, "AsyncClient", lambda **kwargs: client)

    assert await email_service.send_resend_email(
        "user@example.com", "Subject", "Body"
    ) is False


@pytest.mark.asyncio
async def test_email_delivery_resend_api_failure_is_reported(monkeypatch):
    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", "mock-resend-key")
    monkeypatch.setattr(
        email_service,
        "_load_email_configuration",
        _resolved(EmailConfiguration("noreply@example.com", "KPI System", None)),
    )
    client = FakeClient(post_response=FakeResponse(status_code=503))
    monkeypatch.setattr(email_service.httpx, "AsyncClient", lambda **kwargs: client)

    assert await email_service.send_resend_email(
        "user@example.com", "Subject", "Body"
    ) is False


@pytest.mark.asyncio
async def test_test_connection_checks_auth_and_verified_sender_without_sending(monkeypatch):
    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", "mock-resend-key")
    client = FakeClient()
    monkeypatch.setattr(email_service.httpx, "AsyncClient", lambda **kwargs: client)
    configuration = EmailConfiguration(
        from_email="noreply@example.com",
        from_name="KPI System",
        support_email=None,
    )

    ready, message = await email_service.test_resend_connection(configuration)

    assert ready is True
    assert "verified" in message
    assert client.requests[0][0] == "GET"
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_test_connection_rejects_resend_api_failure_and_unverified_domain(monkeypatch):
    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", "mock-resend-key")
    configuration = EmailConfiguration("noreply@example.com", "KPI System", None)
    failed_client = FakeClient(get_response=FakeResponse(status_code=401))
    monkeypatch.setattr(
        email_service.httpx, "AsyncClient", lambda **kwargs: failed_client
    )

    ready, message = await email_service.test_resend_connection(configuration)
    assert ready is False
    assert "verify" in message

    unverified_client = FakeClient(
        get_response=FakeResponse(
            {"data": [{"name": "example.com", "status": "pending"}]}
        )
    )
    monkeypatch.setattr(
        email_service.httpx, "AsyncClient", lambda **kwargs: unverified_client
    )
    ready, message = await email_service.test_resend_connection(configuration)
    assert ready is False
    assert "not verified" in message


@pytest.mark.asyncio
async def test_test_connection_fails_safely_for_missing_api_key_or_sender(monkeypatch):
    configuration = EmailConfiguration(None, "KPI System", None)
    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", None)
    assert await email_service.test_resend_connection(configuration) == (
        False,
        "Resend API key is not configured.",
    )

    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", "mock-resend-key")
    configuration = EmailConfiguration(None, "KPI System", None)
    assert await email_service.test_resend_connection(configuration) == (
        False,
        "From Email is not configured.",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.TimeoutException("timeout"),
        httpx.ConnectError("network unavailable"),
    ],
)
async def test_test_connection_handles_network_failures(monkeypatch, error):
    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", "mock-resend-key")
    monkeypatch.setattr(
        email_service.httpx, "AsyncClient", lambda **kwargs: FakeClient(exception=error)
    )

    ready, message = await email_service.test_resend_connection(
        EmailConfiguration("noreply@example.com", "KPI System", None)
    )

    assert ready is False
    assert "Resend" in message


def _resolved(value):
    async def resolve():
        return value

    return resolve

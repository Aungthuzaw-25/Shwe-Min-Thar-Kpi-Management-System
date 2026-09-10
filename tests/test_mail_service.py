import asyncio

from app.background_jobs import notification_jobs
from app.modules.system.router import _sanitize_setting_value
from app.modules.system.schemas import SystemSettingResponse


def test_email_notifications_are_disabled_by_default(monkeypatch):
    monkeypatch.setattr(notification_jobs.settings, "MAIL_ENABLED", False)
    asyncio.run(notification_jobs.send_email_notification("user@example.com", "Title", "Body"))


def test_email_failures_do_not_log_password(monkeypatch, caplog):
    monkeypatch.setattr(notification_jobs.settings, "MAIL_ENABLED", True)
    monkeypatch.setattr(notification_jobs.settings, "MAIL_HOST", "smtp.example.com")
    monkeypatch.setattr(notification_jobs.settings, "MAIL_PORT", 587)
    monkeypatch.setattr(notification_jobs.settings, "MAIL_USERNAME", "mailer@example.com")
    monkeypatch.setattr(notification_jobs.settings, "MAIL_PASSWORD", "secret-password")
    monkeypatch.setattr(notification_jobs.settings, "MAIL_FROM", "mailer@example.com")

    def fail(*args, **kwargs):
        raise OSError("smtp unavailable")

    monkeypatch.setattr(notification_jobs.smtplib, "SMTP", fail)
    asyncio.run(notification_jobs.send_email_notification("user@example.com", "Title", "Body"))
    assert "secret-password" not in caplog.text


def test_system_settings_never_persist_or_expose_mail_password():
    value = _sanitize_setting_value("email", {"host": "smtp.gmail.com", "password": "secret"})
    assert "password" not in value
    response = SystemSettingResponse.model_validate({
        "id": "00000000-0000-0000-0000-000000000001",
        "key": "email",
        "value": {"host": "smtp.gmail.com", "password": "secret"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    })
    assert "password" not in response.value

import asyncio
import html
import logging
import smtplib
import ssl
from dataclasses import dataclass, field, replace
from email.message import EmailMessage as MIMEEmailMessage
from email.utils import formataddr
from typing import Protocol

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.connection import AsyncSessionLocal
from app.models.system import SystemSetting
from app.modules.system.schemas import (
    EmailConfigurationValue,
    normalize_email_settings,
)

logger = logging.getLogger(__name__)

RESEND_API_BASE_URL = "https://api.resend.com"
EMAIL_PROVIDER_TIMEOUT_SECONDS = 10.0
DEFAULT_FROM_NAME = "Shwe Min Thar KPI Management System"
RESEND_TEST_SENDER = "onboarding@resend.dev"
EMAIL_PROVIDER_UNAVAILABLE_MESSAGE = "Unable to connect to the email provider."


def _create_verified_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    return context


class EmailProviderError(Exception):
    """Base class for normalized email delivery errors."""


class EmailConfigurationError(EmailProviderError):
    """The selected provider is missing required configuration."""


class EmailAuthenticationError(EmailProviderError):
    """The provider rejected its credentials."""


class EmailConnectionError(EmailProviderError):
    """The application could not connect to the provider."""


class EmailSendError(EmailProviderError):
    """The provider rejected the email message."""


class EmailDisabledError(EmailProviderError):
    """Email delivery is disabled because there is no active provider."""


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


@dataclass(frozen=True)
class EmailMessage:
    to: list[str]
    subject: str
    text_body: str | None = None
    html_body: str | None = None
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    reply_to: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    attachments: list[EmailAttachment] = field(default_factory=list)


@dataclass(frozen=True)
class EmailConfiguration:
    email_provider: str
    from_email: str | None
    from_name: str
    support_email: str | None
    smtp_host: str | None
    smtp_port: int | None
    smtp_username: str | None
    smtp_encryption: str


@dataclass(frozen=True)
class EmailSecrets:
    resend_api_key: str | None
    smtp_password: str | None


def _resend_payload(message: EmailMessage, configuration: EmailConfiguration) -> dict:
    payload = {
        "from": _sender(configuration, message),
        "to": message.to,
        "subject": message.subject,
        "reply_to": message.reply_to or configuration.support_email,
    }
    if message.cc:
        payload["cc"] = message.cc
    if message.bcc:
        payload["bcc"] = message.bcc
    if message.text_body is not None:
        payload["text"] = message.text_body
    if message.html_body is not None:
        payload["html"] = message.html_body
    if message.attachments:
        import base64

        payload["attachments"] = [
            {
                "filename": attachment.filename,
                "content": base64.b64encode(attachment.content).decode("ascii"),
            }
            for attachment in message.attachments
        ]
    if not payload["reply_to"]:
        payload.pop("reply_to")
    return payload


def email_configuration_from_value(value: dict | None) -> EmailConfiguration:
    value = value or {}
    try:
        validated = EmailConfigurationValue.model_validate(value or {})
    except ValidationError as exc:
        raise EmailConfigurationError("Saved email configuration is invalid.") from exc
    return EmailConfiguration(
        email_provider=str(value.get("email_provider") or "resend"),
        from_email=str(validated.from_email) if validated.from_email else None,
        from_name=validated.from_name,
        support_email=str(validated.support_email) if validated.support_email else None,
        smtp_host=validated.smtp_host,
        smtp_port=validated.smtp_port,
        smtp_username=validated.smtp_username,
        smtp_encryption=validated.smtp_encryption,
    )


def provider_configuration_from_value(
    provider_name: str, value: dict
) -> EmailConfiguration:
    return email_configuration_from_value(
        {"email_provider": provider_name, **value}
    )


async def get_email_configuration(db: AsyncSession) -> EmailConfiguration:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "email"))
    setting = result.scalar_one_or_none()
    settings_value = normalize_email_settings(setting.value if setting else None)
    return EmailProviderResolver.resolve_value(settings_value)


async def _load_email_settings() -> dict:
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SystemSetting).where(SystemSetting.key == "email")
            )
            setting = result.scalar_one_or_none()
            return normalize_email_settings(setting.value if setting else None)
    except ValidationError as exc:
        raise EmailConfigurationError("Saved email configuration is invalid.") from exc
    except SQLAlchemyError as exc:
        raise EmailConfigurationError("Saved email configuration is unavailable.") from exc


def _load_email_secrets() -> EmailSecrets:
    return EmailSecrets(
        resend_api_key=(settings.RESEND_API_KEY or "").strip() or None,
        smtp_password=(settings.SMTP_PASSWORD or "").strip() or None,
    )


def _support_footer(message: EmailMessage, configuration: EmailConfiguration) -> EmailMessage:
    if not configuration.support_email:
        return message

    footer_text = (
        "\n\n----------------------------------------\n"
        f"{configuration.from_name}\n\n"
        f"Need help? Contact {configuration.support_email}\n"
        "----------------------------------------"
    )
    footer_html = (
        "<hr><p>"
        f"{html.escape(configuration.from_name)}<br><br>"
        "Need help? Contact "
        f'<a href="mailto:{html.escape(configuration.support_email, quote=True)}">'
        f"{html.escape(configuration.support_email)}</a>"
        "</p>"
    )
    return replace(
        message,
        text_body=(message.text_body or "") + footer_text,
        html_body=(
            (message.html_body or "") + footer_html
            if message.html_body is not None
            else None
        ),
        reply_to=message.reply_to or configuration.support_email,
        from_email=message.from_email or configuration.from_email,
        from_name=message.from_name or configuration.from_name,
    )


class EmailProvider(Protocol):
    def validate_configuration(
        self, configuration: EmailConfiguration, secrets: EmailSecrets
    ) -> None: ...

    async def send(
        self, message: EmailMessage, configuration: EmailConfiguration, secrets: EmailSecrets
    ) -> None: ...

    async def test_connection(
        self, configuration: EmailConfiguration, secrets: EmailSecrets
    ) -> str: ...


class EmailProviderFactory:
    _providers: dict[str, type[EmailProvider]] = {}

    @classmethod
    def register(cls, name: str):
        def decorator(provider: type[EmailProvider]):
            cls._providers[name] = provider
            return provider

        return decorator

    @classmethod
    def create(cls, name: str) -> EmailProvider:
        provider = cls._providers.get(name)
        if provider is None:
            raise EmailConfigurationError("Selected email provider is not implemented.")
        return provider()

    @classmethod
    def available_providers(cls) -> tuple[str, ...]:
        return tuple(cls._providers)

    @classmethod
    def definitions(cls) -> tuple[dict, ...]:
        definitions = {
            "resend": {
                "key": "resend",
                "name": "Resend",
                "fields": [],
                "credential_label": "Resend API Key",
            },
            "smtp": {
                "key": "smtp",
                "name": "SMTP",
                "fields": [
                    {"key": "smtp_host", "label": "SMTP Host", "type": "text", "required": True},
                    {"key": "smtp_port", "label": "SMTP Port", "type": "number", "required": True},
                    {"key": "smtp_username", "label": "SMTP Username", "type": "text"},
                    {
                        "key": "smtp_encryption",
                        "label": "Encryption",
                        "type": "select",
                        "options": [
                            {"value": "starttls", "label": "STARTTLS"},
                            {"value": "ssl", "label": "SSL/TLS"},
                            {"value": "none", "label": "None"},
                        ],
                    },
                ],
                "credential_label": "SMTP Password",
            },
        }
        return tuple(definitions[key] for key in cls._providers)


class EmailProviderResolver:
    @classmethod
    def resolve_value(
        cls, settings_value: dict, provider_name: str | None = None
    ) -> EmailConfiguration:
        normalized = normalize_email_settings(settings_value)
        name = provider_name or normalized.get("active_provider")
        if not name:
            raise EmailDisabledError("Email delivery is disabled — no active provider.")
        provider_settings = normalized["providers"].get(name)
        if provider_settings is None:
            raise EmailConfigurationError("Email provider is not configured.")
        if provider_name is None and not provider_settings.get("enabled"):
            raise EmailDisabledError("Email delivery is disabled — no active provider.")
        return email_configuration_from_value(
            {"email_provider": name, **provider_settings}
        )

    @classmethod
    def resolve_provider(
        cls, settings_value: dict, provider_name: str | None = None
    ) -> tuple[EmailProvider, EmailConfiguration]:
        configuration = cls.resolve_value(settings_value, provider_name)
        return (
            EmailProviderFactory.create(configuration.email_provider),
            configuration,
        )


def _sender(configuration: EmailConfiguration, message: EmailMessage) -> str:
    from_email = message.from_email or configuration.from_email
    if not from_email:
        raise EmailConfigurationError("From Email is required.")
    from_name = message.from_name or configuration.from_name
    return formataddr((from_name, from_email))


def _require_sender(configuration: EmailConfiguration) -> None:
    if not configuration.from_email:
        raise EmailConfigurationError("From Email is required.")


@EmailProviderFactory.register("resend")
class ResendProvider:
    def _api_key(self, secrets: EmailSecrets) -> str:
        if not secrets.resend_api_key:
            raise EmailConfigurationError("Resend API key is not configured.")
        return secrets.resend_api_key

    def _validate(self, configuration: EmailConfiguration, secrets: EmailSecrets) -> None:
        _require_sender(configuration)
        self._api_key(secrets)

    def validate_configuration(
        self, configuration: EmailConfiguration, secrets: EmailSecrets
    ) -> None:
        self._validate(configuration, secrets)

    async def send(
        self, message: EmailMessage, configuration: EmailConfiguration, secrets: EmailSecrets
    ) -> None:
        self._validate(configuration, secrets)
        api_key = self._api_key(secrets)
        payload = _resend_payload(message, configuration)

        try:
            async with httpx.AsyncClient(timeout=EMAIL_PROVIDER_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{RESEND_API_BASE_URL}/emails",
                    headers={"Authorization": "Bearer " + api_key},
                    json=payload,
                )
                if response.status_code in {401, 403}:
                    raise EmailAuthenticationError("Resend rejected the configured credentials.")
                if response.status_code in {400, 422}:
                    raise EmailSendError("Resend rejected the sender or email message.")
                response.raise_for_status()
        except EmailProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise EmailConnectionError("Email provider connection timed out.") from exc
        except httpx.HTTPError as exc:
            raise EmailConnectionError(EMAIL_PROVIDER_UNAVAILABLE_MESSAGE) from exc

    async def test_connection(
        self, configuration: EmailConfiguration, secrets: EmailSecrets
    ) -> str:
        self._validate(configuration, secrets)
        api_key = self._api_key(secrets)
        test_mode = configuration.from_email.lower() == RESEND_TEST_SENDER
        endpoint = f"{RESEND_API_BASE_URL}/domains"
        try:
            async with httpx.AsyncClient(timeout=EMAIL_PROVIDER_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    endpoint,
                    headers={"Authorization": "Bearer " + api_key},
                )
                if response.status_code in {401, 403}:
                    raise EmailAuthenticationError("Provider authentication failed.")
                response.raise_for_status()
                response_body = response.json()
        except EmailProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise EmailConnectionError(EMAIL_PROVIDER_UNAVAILABLE_MESSAGE) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise EmailConnectionError(EMAIL_PROVIDER_UNAVAILABLE_MESSAGE) from exc

        if not isinstance(response_body, dict) or not isinstance(
            response_body.get("data"), list
        ):
            raise EmailConnectionError("Provider returned an invalid connection response.")
        if test_mode:
            return "Ready — Resend Test Mode"

        domain = configuration.from_email.rsplit("@", maxsplit=1)[-1].lower()
        if not any(
            isinstance(item, dict)
            and str(item.get("name", "")).lower() == domain
            and item.get("status") == "verified"
            for item in response_body["data"]
        ):
            raise EmailConfigurationError("Provider rejected the sender: domain is not verified.")
        return "Ready"


@EmailProviderFactory.register("smtp")
class SMTPProvider:
    def _validate(self, configuration: EmailConfiguration, secrets: EmailSecrets) -> None:
        _require_sender(configuration)
        if not configuration.smtp_host:
            raise EmailConfigurationError("SMTP Host is required.")
        if configuration.smtp_port is None or not 1 <= configuration.smtp_port <= 65535:
            raise EmailConfigurationError("SMTP Port must be between 1 and 65535.")
        if configuration.smtp_encryption not in {"starttls", "ssl", "none"}:
            raise EmailConfigurationError("SMTP encryption setting is invalid.")
        if configuration.smtp_username and not secrets.smtp_password:
            raise EmailConfigurationError("SMTP password is not configured.")

    def validate_configuration(
        self, configuration: EmailConfiguration, secrets: EmailSecrets
    ) -> None:
        self._validate(configuration, secrets)

    def _connect(self, configuration: EmailConfiguration, secrets: EmailSecrets):
        self._validate(configuration, secrets)
        smtp = None
        try:
            if configuration.smtp_encryption == "ssl":
                smtp = smtplib.SMTP_SSL(
                    configuration.smtp_host,
                    configuration.smtp_port,
                    timeout=EMAIL_PROVIDER_TIMEOUT_SECONDS,
                    context=_create_verified_ssl_context(),
                )
            else:
                smtp = smtplib.SMTP(
                    configuration.smtp_host,
                    configuration.smtp_port,
                    timeout=EMAIL_PROVIDER_TIMEOUT_SECONDS,
                )
                smtp.ehlo()
                if configuration.smtp_encryption == "starttls":
                    smtp.starttls(context=_create_verified_ssl_context())
                    smtp.ehlo()
            if configuration.smtp_username:
                smtp.login(configuration.smtp_username, secrets.smtp_password)
            return smtp
        except smtplib.SMTPAuthenticationError as exc:
            if smtp is not None:
                smtp.close()
            raise EmailAuthenticationError("SMTP authentication failed.") from exc
        except (OSError, smtplib.SMTPException) as exc:
            if smtp is not None:
                smtp.close()
            raise EmailConnectionError("Unable to connect to the SMTP server.") from exc

    async def send(
        self, message: EmailMessage, configuration: EmailConfiguration, secrets: EmailSecrets
    ) -> None:
        def send_sync() -> None:
            smtp = self._connect(configuration, secrets)
            try:
                mail = MIMEEmailMessage()
                mail["Subject"] = message.subject
                mail["From"] = _sender(configuration, message)
                mail["To"] = ", ".join(message.to)
                if message.cc:
                    mail["Cc"] = ", ".join(message.cc)
                if message.reply_to or configuration.support_email:
                    mail["Reply-To"] = message.reply_to or configuration.support_email
                if message.text_body is not None:
                    mail.set_content(message.text_body)
                if message.html_body is not None:
                    mail.add_alternative(message.html_body, subtype="html")
                for attachment in message.attachments:
                    main_type, _, sub_type = attachment.content_type.partition("/")
                    mail.add_attachment(
                        attachment.content,
                        maintype=main_type or "application",
                        subtype=sub_type or "octet-stream",
                        filename=attachment.filename,
                    )
                smtp.send_message(
                    mail,
                    to_addrs=message.to + message.cc + message.bcc,
                )
            except smtplib.SMTPAuthenticationError as exc:
                raise EmailAuthenticationError("SMTP authentication failed.") from exc
            except (OSError, smtplib.SMTPException) as exc:
                raise EmailSendError("SMTP server rejected the email message.") from exc
            finally:
                try:
                    smtp.quit()
                except (OSError, smtplib.SMTPException):
                    smtp.close()

        await asyncio.to_thread(send_sync)

    async def test_connection(
        self, configuration: EmailConfiguration, secrets: EmailSecrets
    ) -> str:
        def test_sync() -> None:
            smtp = self._connect(configuration, secrets)
            try:
                return None
            finally:
                try:
                    smtp.quit()
                except (OSError, smtplib.SMTPException):
                    smtp.close()

        await asyncio.to_thread(test_sync)
        return "Ready"


class EmailService:
    async def _settings(self) -> dict:
        return await _load_email_settings()

    def validate_configuration(
        self, provider: EmailProvider, configuration: EmailConfiguration
    ) -> None:
        provider.validate_configuration(configuration, _load_email_secrets())

    def validate_provider_configuration(
        self, provider_name: str, provider_value: dict
    ) -> None:
        provider, configuration = EmailProviderResolver.resolve_provider(
            {
                "providers": {provider_name: provider_value},
                "active_provider": None,
            },
            provider_name,
        )
        self.validate_configuration(provider, configuration)

    async def test_provider(self, provider_name: str, provider_value: dict) -> str:
        provider, configuration = EmailProviderResolver.resolve_provider(
            {
                "providers": {provider_name: provider_value},
                "active_provider": None,
            },
            provider_name,
        )
        return await self.test_configuration(provider, configuration)

    async def send(self, message: EmailMessage) -> bool:
        provider_name = "unknown"
        try:
            settings_value = await self._settings()
            provider, configuration = EmailProviderResolver.resolve_provider(
                settings_value
            )
            provider_name = configuration.email_provider
            secrets = _load_email_secrets()
            provider.validate_configuration(configuration, secrets)
            message = _support_footer(message, configuration)
            await provider.send(message, configuration, secrets)
        except EmailProviderError as exc:
            logger.warning(
                "email provider=%s operation=send result=failed reason=%s",
                provider_name,
                str(exc),
            )
            return False
        logger.info("email provider=%s operation=send result=success", configuration.email_provider)
        return True

    async def test_connection(self, provider_name: str | None = None) -> str:
        settings_value = await self._settings()
        provider, configuration = EmailProviderResolver.resolve_provider(
            settings_value, provider_name
        )
        return await self.test_configuration(provider, configuration)

    async def test_configuration(
        self, provider: EmailProvider, configuration: EmailConfiguration
    ) -> str:
        try:
            result = await provider.test_connection(configuration, _load_email_secrets())
        except EmailProviderError as exc:
            logger.warning(
                "email provider=%s operation=test_connection result=failed reason=%s",
                configuration.email_provider,
                str(exc),
            )
            raise
        logger.info(
            "email provider=%s operation=test_connection result=success",
            configuration.email_provider,
        )
        return result


email_service = EmailService()

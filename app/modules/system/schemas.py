from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from app.models.system import LogLevel


class SystemSettingCreate(BaseModel):
    key: str
    value: dict[str, Any]
    description: str | None = None

class SystemSettingUpdate(BaseModel):
    value: dict[str, Any] | None = None
    description: str | None = None


# ---------------------------------------------------------------------------
# Company trademark (branding): the company name lives in the existing
# ``general`` settings value; the logo reference lives in a ``branding``
# settings value that only the branding endpoints may write.
# ---------------------------------------------------------------------------

COMPANY_NAME_MAX_LENGTH = 60
COMPANY_TAGLINE_MAX_LENGTH = 90

# Fields that describe the stored logo. They are managed exclusively by the
# logo upload/reset endpoints, so generic settings saves must never be able
# to forge them (e.g. point the public logo at an arbitrary object).
LOGO_REFERENCE_FIELDS = {
    "logo",
    "logo_url",
    "logo_object",
    "logo_storage",
    "logo_updated_at",
}


def validate_company_name(value) -> str:
    """Return the trimmed company name or raise ``ValueError``.

    Plain text only: no markup, no line breaks, no control characters
    (the same rule the email ``from_name`` field already follows).
    """
    if not isinstance(value, str):
        raise ValueError("Company name must be plain text.")
    name = value.strip()
    if not name:
        raise ValueError("Company name is required.")
    if len(name) > COMPANY_NAME_MAX_LENGTH:
        raise ValueError(
            f"Company name must be at most {COMPANY_NAME_MAX_LENGTH} characters."
        )
    if any(
        character in name for character in "<>\r\n"
    ) or any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise ValueError("Company name must be plain text.")
    return name


def validate_company_tagline(value) -> str:
    """Return the trimmed tagline or raise ``ValueError``.

    Same plain-text rules as the company name, with its own length
    limit. An empty tagline is allowed — it means "show no subtitle".
    """
    if not isinstance(value, str):
        raise ValueError("Company tagline must be plain text.")
    tagline = value.strip()
    if len(tagline) > COMPANY_TAGLINE_MAX_LENGTH:
        raise ValueError(
            f"Company tagline must be at most {COMPANY_TAGLINE_MAX_LENGTH} characters."
        )
    if any(
        character in tagline for character in "<>\r\n"
    ) or any(ord(character) < 32 or ord(character) == 127 for character in tagline):
        raise ValueError("Company tagline must be plain text.")
    return tagline


def normalize_general_settings(value: dict) -> dict:
    """Validate the ``general`` settings value for saving.

    ``company_name`` / ``company_tagline`` are checked with the
    trademark rules when present. Logo reference fields are dropped:
    the logo can only change through the logo upload/reset endpoints.
    Other fields (timezone, date_format) pass through unchanged,
    exactly as before.
    """
    sanitized = {
        key: item
        for key, item in dict(value).items()
        if key not in LOGO_REFERENCE_FIELDS
    }
    if sanitized.get("company_name") is not None:
        sanitized["company_name"] = validate_company_name(
            sanitized["company_name"]
        )
    if sanitized.get("company_tagline") is not None:
        sanitized["company_tagline"] = validate_company_tagline(
            sanitized["company_tagline"]
        )
    return sanitized


class CompanyTrademarkUpdate(BaseModel):
    company_name: str
    # Optional: omitting it leaves the saved tagline untouched; sending
    # an empty string clears the tagline (no subtitle shown).
    company_tagline: str | None = None

    @field_validator("company_name", mode="before")
    @classmethod
    def normalize_company_name(cls, value):
        return validate_company_name(value)

    @field_validator("company_tagline", mode="before")
    @classmethod
    def normalize_company_tagline(cls, value):
        if value is None:
            return None
        return validate_company_tagline(value)


class BrandResponse(BaseModel):
    company_name: str
    company_tagline: str
    logo_url: str
    has_custom_logo: bool
    logo_updated_at: int | None = None


class PublicBrandResponse(BrandResponse):
    # Login-provider truth for the unauthenticated login page: a provider
    # flag is True only when that provider is fully configured.
    oauth_providers: dict[str, bool]


class PasswordPolicyValue(BaseModel):
    minimum_password_length: StrictInt = Field(ge=8, le=128)
    require_complexity: StrictBool
    force_password_change_days: StrictInt = Field(ge=0, le=3650)


class EmailConfigurationValue(BaseModel):
    enabled: StrictBool = False
    from_email: EmailStr | None = None
    from_name: str = Field(default="Shwe Min Thar KPI Management System", max_length=200)
    support_email: EmailStr | None = None
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: StrictInt | None = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, max_length=255)
    smtp_encryption: str = "starttls"
    tested: StrictBool = False
    last_test_status: Literal["ready", "not_ready"] | None = None

    @field_validator("from_email", "support_email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Email addresses must be strings.")
        value = value.strip()
        return value or None

    @field_validator("smtp_host", "smtp_username", mode="before")
    @classmethod
    def normalize_smtp_text(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("SMTP configuration values must be text.")
        value = value.strip()
        return value or None

    @field_validator("smtp_encryption")
    @classmethod
    def validate_smtp_encryption(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"starttls", "ssl", "none"}:
            raise ValueError("SMTP encryption must be STARTTLS, SSL, or None.")
        return value

    @field_validator("from_name")
    @classmethod
    def normalize_from_name(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("From Name must be plain text.")
        value = value.strip()
        if not value or any(character in value for character in "<>\r\n"):
            raise ValueError("From Name must be plain text.")
        return value


class EmailSettingsValue(BaseModel):
    providers: dict[str, EmailConfigurationValue] = Field(default_factory=dict)
    active_provider: str | None = None

    @model_validator(mode="after")
    def validate_active_provider(self):
        if any(provider not in {"resend", "smtp"} for provider in self.providers):
            raise ValueError("Email settings contain an unsupported provider.")
        enabled_providers = [
            provider
            for provider, config in self.providers.items()
            if config.enabled
        ]
        if self.active_provider is not None:
            active = self.providers.get(self.active_provider)
            if active is None or not active.enabled:
                raise ValueError("The active email provider must be configured and enabled.")
        if len(enabled_providers) > 1 or (
            enabled_providers
            and self.active_provider != enabled_providers[0]
        ):
            raise ValueError("Exactly one enabled provider must be the active provider.")
        return self


class EmailProviderSaveRequest(BaseModel):
    provider: str
    configuration: EmailConfigurationValue

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"resend", "smtp"}:
            raise ValueError("Email provider must be Resend or SMTP.")
        return value


class EmailProviderLifecycleRequest(BaseModel):
    action: str

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"enable", "disable"}:
            raise ValueError("Provider action must be enable or disable.")
        return value


class LegacyEmailConfigurationValue(EmailConfigurationValue):
    email_provider: str = "resend"

    @field_validator("email_provider")
    @classmethod
    def require_resend(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"resend", "smtp"}:
            raise ValueError("Email provider must be Resend or SMTP.")
        return value


def normalize_email_settings(value: dict | None) -> dict:
    value = value or {}
    if "providers" in value or "active_provider" in value:
        validated = EmailSettingsValue.model_validate(value)
        providers = {}
        for provider, configuration in validated.providers.items():
            excluded = (
                {"smtp_host", "smtp_port", "smtp_username", "smtp_encryption"}
                if provider == "resend"
                else set()
            )
            providers[provider] = configuration.model_dump(
                mode="json", exclude=excluded
            )
        return {
            "providers": providers,
            "active_provider": validated.active_provider,
        }

    if not value:
        return EmailSettingsValue().model_dump(mode="json")

    legacy = LegacyEmailConfigurationValue.model_validate(value)
    provider = legacy.email_provider
    configuration = legacy.model_dump(
        exclude={"email_provider", "enabled", "tested", "last_test_status"}
    )
    configuration["enabled"] = False
    configuration["tested"] = False
    configuration["last_test_status"] = None
    normalized = EmailSettingsValue(
        providers={provider: EmailConfigurationValue.model_validate(configuration)},
        active_provider=None,
    ).model_dump(mode="json")
    return normalize_email_settings(normalized)


class SystemSettingResponse(BaseModel):
    id: UUID
    key: str
    value: dict[str, Any]
    description: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    # 🌟 FIX: Updated to Pydantic V2 syntax
    model_config = ConfigDict(from_attributes=True)

    @field_validator("value", mode="before")
    @classmethod
    def redact_mail_credentials(cls, value, info):
        setting_key = str(info.data.get("key", "")).lower().replace("-", "_")
        if "api_key" in setting_key or "apikey" in setting_key:
            return {}
        if isinstance(value, dict) and setting_key == "email":
            allowed = {
                "providers",
                "active_provider",
            }
            if "providers" in value:
                return {
                    key: cls._without_secrets(item)
                    for key, item in value.items()
                    if key in allowed
                }
            legacy_allowed = {
                "email_provider", "from_email", "from_name", "support_email",
                "smtp_host", "smtp_port", "smtp_username", "smtp_encryption",
                "resend_api_key_configured", "smtp_password_configured",
            }
            return {key: item for key, item in value.items() if key in legacy_allowed}
        return cls._without_secrets(value)

    @classmethod
    def _without_secrets(cls, value):
        def is_secret(field: str) -> bool:
            normalized = field.lower().replace("-", "_")
            if normalized.endswith("_configured"):
                return False
            return normalized in {
                "password",
                "secret",
                "token",
                "api_key",
                "apikey",
                "access_key",
                "private_key",
                "credential",
            } or normalized.endswith(
                (
                    "_password",
                    "_secret",
                    "_token",
                    "_api_key",
                    "_apikey",
                    "_access_key",
                    "_private_key",
                    "_credential",
                )
            )

        if isinstance(value, dict):
            return {
                key: cls._without_secrets(item)
                for key, item in value.items()
                if not is_secret(str(key))
            }
        if isinstance(value, list):
            return [cls._without_secrets(item) for item in value]
        return value

class SystemEventLogResponse(BaseModel):
    id: UUID
    event_type: str
    level: LogLevel
    message: str
    details: dict[str, Any] | None = None
    created_at: datetime | None = None

    # 🌟 FIX: Updated to Pydantic V2 syntax
    model_config = ConfigDict(from_attributes=True)


class SystemEventLogDetailResponse(SystemEventLogResponse):
    # Support-only: the raw stored message, kept out of the default
    # list view (whose `message` is always the plain-language text —
    # see app.services.system_log_messages). The UI should show this
    # only behind an explicit "technical details" affordance.
    technical_message: str | None = None


class SystemEventLogPage(BaseModel):
    items: list[SystemEventLogResponse]
    total: int
    page: int
    page_size: int
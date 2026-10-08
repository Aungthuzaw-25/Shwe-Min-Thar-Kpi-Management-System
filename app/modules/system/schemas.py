from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
)

from app.models.system import LogLevel


class SystemSettingCreate(BaseModel):
    key: str
    value: dict[str, Any]
    description: str | None = None

class SystemSettingUpdate(BaseModel):
    value: dict[str, Any] | None = None
    description: str | None = None


class PasswordPolicyValue(BaseModel):
    minimum_password_length: StrictInt = Field(ge=8, le=128)
    require_complexity: StrictBool
    force_password_change_days: StrictInt = Field(ge=0, le=3650)


class EmailConfigurationValue(BaseModel):
    email_provider: str = "resend"
    from_email: EmailStr | None = None
    from_name: str = Field(default="Shwe Min Thar KPI Management System", max_length=200)
    support_email: EmailStr | None = None
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: StrictInt | None = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, max_length=255)
    smtp_encryption: str = "starttls"

    @field_validator("from_email", "support_email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Email addresses must be strings.")
        value = value.strip()
        return value or None

    @field_validator("email_provider")
    @classmethod
    def require_resend(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"resend", "smtp"}:
            raise ValueError("Email provider must be Resend or SMTP.")
        return value

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
                "email_provider",
                "from_email",
                "from_name",
                "support_email",
                "smtp_host",
                "smtp_port",
                "smtp_username",
                "smtp_encryption",
                "resend_api_key_configured",
                "smtp_password_configured",
            }
            return {key: item for key, item in value.items() if key in allowed}
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
    pass


class SystemEventLogPage(BaseModel):
    items: list[SystemEventLogResponse]
    total: int
    page: int
    page_size: int
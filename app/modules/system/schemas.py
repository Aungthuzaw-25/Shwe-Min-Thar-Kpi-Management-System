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
        if value != "resend":
            raise ValueError("Email provider must be Resend.")
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
        if (
            isinstance(value, dict)
            and setting_key == "email"
        ):
            allowed = {
                "email_provider",
                "from_email",
                "from_name",
                "support_email",
                "resend_api_key_configured",
            }
            return {key: item for key, item in value.items() if key in allowed}
        if isinstance(value, dict) and "password" in value:
            return {key: item for key, item in value.items() if key != "password"}
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
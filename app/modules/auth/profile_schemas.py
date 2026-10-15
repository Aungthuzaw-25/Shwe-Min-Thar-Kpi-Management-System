import re
import unicodedata
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictStr, field_validator


class ProfileResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    email: EmailStr
    region_town: str | None = None
    phone_number: str | None = None
    bio: str | None = None
    avatar_url: str | None = None


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    first_name: str | None = Field(default=None, min_length=1, max_length=100, strict=True)
    last_name: str | None = Field(default=None, min_length=1, max_length=100, strict=True)
    region_town: str | None = Field(default=None, max_length=100, strict=True)
    phone_number: str | None = Field(default=None, max_length=32, strict=True)
    bio: str | None = Field(default=None, max_length=500, strict=True)

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(ord(char) < 32 for char in value)):
            raise ValueError("Name must contain visible characters only.")
        return value

    @field_validator("region_town", "bio")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is not None and any(ord(char) < 32 and char not in "\t\n" for char in value):
            raise ValueError("Control characters are not allowed.")
        return value

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, value: str | None) -> str | None:
        if value is not None and value and not re.fullmatch(r"[0-9+(). -]{3,32}", value):
            raise ValueError("Enter a valid phone number.")
        return value or None


class AccountProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str = Field(min_length=1, max_length=80, strict=True)
    phone: str | None = Field(default=None, max_length=20, strict=True)

    @field_validator("display_name", mode="before")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        if not isinstance(value, str) or any(unicodedata.category(char) == "Cc" for char in value):
            raise ValueError("Display name must contain visible characters only.")
        return unicodedata.normalize("NFC", " ".join(value.split()))

    @field_validator("phone")
    @classmethod
    def validate_account_phone(cls, value: str | None) -> str | None:
        value = value.strip() if value is not None else None
        if value and not re.fullmatch(r"[0-9+() -]{6,20}", value):
            raise ValueError("Enter a valid phone number.")
        return value or None


class AccountPasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    new_password: StrictStr = Field(min_length=1, max_length=256)
    confirm_password: StrictStr = Field(min_length=1, max_length=256)


class AccountReauthentication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: StrictStr = Field(min_length=1, max_length=256)

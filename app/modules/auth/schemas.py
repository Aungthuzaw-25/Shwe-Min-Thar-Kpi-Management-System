
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool, StrictInt, StrictStr


class LoginRequest(BaseModel):
    email: str
    password: str
    remember_me: bool = False
    # SA second factor: the current 6-digit authenticator code.
    # Required only for Super Admins with a confirmed TOTP
    # enrollment; ignored for everyone else.
    totp_code: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    must_change_password: bool = False
    user: dict | None = None

class MySessionRow(BaseModel):
    """One OWN active session for the /me/sessions list.

    Only user-meaningful fields: never a token, token hash, raw
    user_agent, or device_id hash.
    """

    id: UUID
    device_label: str
    last_used_at: datetime
    expires_at: datetime
    remember_me: bool
    is_current: bool
    ip_address: str | None


class MySessionListResponse(BaseModel):
    sessions: list[MySessionRow]


class RefreshTokenRequest(BaseModel):
    refresh_token: str
    
class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_password: StrictStr = Field(min_length=1, max_length=256)
    new_password: StrictStr = Field(min_length=1, max_length=256)
    # Optional for API compatibility; supported clients send it and the router
    # rejects a mismatch before any password state is changed.
    confirm_new_password: StrictStr | None = Field(default=None, max_length=256)


class UnlockRequestCreate(BaseModel):
    email: EmailStr


class UnlockRequestResponse(BaseModel):
    accepted: bool
    message: str


class UserSecuritySettingsUpdate(BaseModel):
    max_failed_attempts: StrictInt = Field(ge=1, le=2147483647)
    lockout_duration_minutes: StrictInt = Field(ge=1, le=2147483647)
    superadmin_lockout_enabled: StrictBool


class UserSecuritySettingsResponse(UserSecuritySettingsUpdate):
    model_config = ConfigDict(from_attributes=True)

    updated_by: UUID | None = None
    updated_at: datetime | None = None


class LockedUserResponse(BaseModel):
    user_id: UUID
    user_name: str
    email: str
    role: str
    failed_login_attempts: int
    last_failed_at: datetime | None = None
    last_failed_ip: str | None = None
    locked_until: datetime | None = None
    pending_request_id: UUID | None = None


class UnlockRequestDetailResponse(BaseModel):
    request_id: UUID
    user_id: UUID
    user_name: str
    email: str
    role: str
    failed_attempts: int
    locked_at: datetime
    locked_until: datetime | None = None
    last_failed_at: datetime | None = None
    last_failed_ip: str | None = None
    last_failed_user_agent: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    status: str
    created_at: datetime

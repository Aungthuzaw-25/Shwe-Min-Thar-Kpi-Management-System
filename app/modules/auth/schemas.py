
from pydantic import BaseModel, ConfigDict, Field, StrictStr


class LoginRequest(BaseModel):
    email: str
    password: str
    remember_me: bool = False


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    must_change_password: bool = False
    user: dict | None = None

class RefreshTokenRequest(BaseModel):
    refresh_token: str
    
class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_password: StrictStr = Field(min_length=1, max_length=256)
    new_password: StrictStr = Field(min_length=1, max_length=256)
    # Optional for API compatibility; supported clients send it and the router
    # rejects a mismatch before any password state is changed.
    confirm_new_password: StrictStr | None = Field(default=None, max_length=256)

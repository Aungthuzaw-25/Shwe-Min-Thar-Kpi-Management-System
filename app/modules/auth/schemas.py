
from pydantic import BaseModel


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
    old_password: str
    new_password: str
    # Optional for API compatibility; supported clients send it and the router
    # rejects a mismatch before any password state is changed.
    confirm_new_password: str | None = None

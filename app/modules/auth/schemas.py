from pydantic import BaseModel
from typing import Optional

class LoginRequest(BaseModel):
    email: str
    password: str
    remember_me: bool = False


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    must_change_password: bool = False
    user: Optional[dict] = None

class RefreshTokenRequest(BaseModel):
    refresh_token: str
    
class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str
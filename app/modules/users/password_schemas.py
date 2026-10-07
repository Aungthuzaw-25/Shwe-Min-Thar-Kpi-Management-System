
from pydantic import BaseModel


class PasswordResetAdmin(BaseModel):
    new_password: str
    confirm_new_password: str | None = None

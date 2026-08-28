from pydantic import BaseModel

class PasswordResetAdmin(BaseModel):
    new_password: str

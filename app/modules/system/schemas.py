from pydantic import BaseModel, ConfigDict, field_validator # 🌟 FIX: Imported ConfigDict
from typing import Optional, Any, Dict
from datetime import datetime
from uuid import UUID
from app.models.system import LogLevel

class SystemSettingCreate(BaseModel):
    key: str
    value: Dict[str, Any]
    description: Optional[str] = None

class SystemSettingUpdate(BaseModel):
    value: Optional[Dict[str, Any]] = None
    description: Optional[str] = None

class SystemSettingResponse(BaseModel):
    id: UUID
    key: str
    value: Dict[str, Any]
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # 🌟 FIX: Updated to Pydantic V2 syntax
    model_config = ConfigDict(from_attributes=True)

    @field_validator("value", mode="before")
    @classmethod
    def redact_mail_password(cls, value):
        if isinstance(value, dict) and "password" in value:
            return {key: item for key, item in value.items() if key != "password"}
        return value

class SystemEventLogResponse(BaseModel):
    id: UUID
    event_type: str
    level: LogLevel
    message: str
    details: Optional[Dict[str, Any]] = None
    created_at: datetime

    # 🌟 FIX: Updated to Pydantic V2 syntax
    model_config = ConfigDict(from_attributes=True)
from pydantic import BaseModel, ConfigDict # 🌟 FIX: Imported ConfigDict
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

class SystemEventLogResponse(BaseModel):
    id: UUID
    event_type: str
    level: LogLevel
    message: str
    details: Optional[Dict[str, Any]] = None
    created_at: datetime

    # 🌟 FIX: Updated to Pydantic V2 syntax
    model_config = ConfigDict(from_attributes=True)
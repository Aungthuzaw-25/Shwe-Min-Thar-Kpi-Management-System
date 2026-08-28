from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID
from datetime import datetime

class PositionCreate(BaseModel):
    name: str = Field(..., description="Name of the position (e.g., Senior Developer)")
    department_id: UUID
    parent_position_id: Optional[UUID] = Field(None, description="Direct manager's position ID")
    is_active: bool = True

class PositionUpdate(BaseModel):
    name: Optional[str] = None
    department_id: Optional[UUID] = None
    parent_position_id: Optional[UUID] = None
    is_active: Optional[bool] = None

class PositionResponse(BaseModel):
    id: UUID
    name: str
    department_id: UUID
    parent_position_id: Optional[UUID]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True
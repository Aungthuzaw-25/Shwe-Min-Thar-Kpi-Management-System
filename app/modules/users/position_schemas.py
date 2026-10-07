from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PositionCreate(BaseModel):
    name: str = Field(..., description="Name of the position (e.g., Senior Developer)")
    department_id: UUID
    parent_position_id: UUID | None = Field(None, description="Direct manager's position ID")
    is_active: bool = True

class PositionUpdate(BaseModel):
    name: str | None = None
    department_id: UUID | None = None
    parent_position_id: UUID | None = None
    is_active: bool | None = None

class PositionResponse(BaseModel):
    id: UUID
    name: str
    department_id: UUID
    parent_position_id: UUID | None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True
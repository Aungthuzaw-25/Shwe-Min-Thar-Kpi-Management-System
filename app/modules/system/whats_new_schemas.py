"""Schemas for what's-new entries (Database design Stage 3).

Burmese-only copy for launch; no *_en fields.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.system import WhatsNewStatus, WhatsNewVisibility


class WhatsNewEntryCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1)
    how_to: str | None = None
    link_path: str | None = Field(default=None, max_length=255)
    link_label: str | None = Field(default=None, max_length=100)
    image_path: str | None = Field(default=None, max_length=255)
    visible_to: WhatsNewVisibility = WhatsNewVisibility.ALL


class WhatsNewEntryUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1)
    how_to: str | None = None
    link_path: str | None = Field(default=None, max_length=255)
    link_label: str | None = Field(default=None, max_length=100)
    image_path: str | None = Field(default=None, max_length=255)
    visible_to: WhatsNewVisibility | None = None


class WhatsNewEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    body: str
    how_to: str | None
    link_path: str | None
    link_label: str | None
    image_path: str | None
    visible_to: WhatsNewVisibility
    status: WhatsNewStatus
    published_at: datetime | None
    created_by_id: UUID | None
    created_at: datetime | None
    updated_at: datetime | None

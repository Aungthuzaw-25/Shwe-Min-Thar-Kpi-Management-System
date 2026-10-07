from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NotificationResponse(BaseModel):
    id: UUID
    title: str
    message: str
    notification_type: str
    is_read: bool
    channel: str
    group_key: str | None = None
    created_at: datetime

    # 🌟 Pydantic V2 အတွက် class Config အစား model_config ကို ပြောင်းလဲအသုံးပြုထားပါသည်
    model_config = ConfigDict(from_attributes=True)

class NotificationCreate(BaseModel):
    user_id: UUID
    title: str
    message: str
    notification_type: str
    channel: str = "DASHBOARD"
    group_key: str | None = None
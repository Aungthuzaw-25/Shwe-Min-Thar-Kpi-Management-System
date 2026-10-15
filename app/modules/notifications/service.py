import logging
from uuid import UUID

from fastapi import BackgroundTasks

from app.background_jobs.notification_jobs import send_email_notification
from app.database.connection import AsyncSessionLocal
from app.modules.notifications.repo import create_notification

# 🌟 CRITICAL FIX: UserRepository အစား get_user_by_id ကို ခေါ်ယူပါမည်
from app.modules.users.repo import get_user_by_id

logger = logging.getLogger(__name__)


class NotificationService:
    @staticmethod
    async def trigger_notification(
        user_id: UUID,
        title: str,
        message: str,
        notification_type: str, 
        background_tasks: BackgroundTasks, 
        channel: str = "DASHBOARD",
        group_key: str | None = None
    ):
        # 1. Database သို့ သိမ်းခြင်း
        notification_data = {
            "user_id": user_id,
            "title": title,
            "message": message,
            "notification_type": notification_type,
            "channel": channel,
            "group_key": group_key
        }
        async with AsyncSessionLocal() as db:
            try:
                notification = await create_notification(db, notification_data, commit=False)

                # 2. FastAPI Native Background Task ဖြင့် Email ပို့ခြင်း
                if channel in ["EMAIL", "BOTH"]:
                    user = await get_user_by_id(db, user_id)
                    if user and user.email:
                        background_tasks.add_task(
                            send_email_notification, user.email, title, message
                        )
                await db.commit()
                return notification
            except Exception:
                await db.rollback()
                logger.exception("Failed to create notification for user %s", user_id)
                raise
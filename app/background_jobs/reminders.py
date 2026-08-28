"""
Background Reminders & Notification Dispatcher
"""
import logging
from datetime import datetime, timedelta
import pytz
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.database.connection import AsyncSessionLocal
from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.notifications.repo import create_notification
from app.core.timezone import APPLICATION_TIMEZONE

logger = logging.getLogger(__name__)
timezone = APPLICATION_TIMEZONE

async def check_and_send_task_reminders(db: AsyncSession):
    """Due Date နီးကပ်လာသော (၂၄ နာရီအတွင်း) သို့မဟုတ် Overdue ဖြစ်နေသော Task များကို အသိပေးခြင်း"""
    now = datetime.now(timezone)
    tomorrow = now + timedelta(days=1)
    
    # 1. 24 နာရီအတွင်း Due Date ရောက်မည့် Task များ
    stmt_due = select(Task).options(selectinload(Task.owner)).where(
        Task.status.in_([TaskStatus.DRAFT, TaskStatus.PENDING_L1]),
        Task.due_date <= tomorrow,
        Task.due_date >= now,
        Task.is_deleted == False
    )
    result_due = await db.execute(stmt_due)
    due_tasks = result_due.scalars().all()
    
    for t in due_tasks:
        try:
            await create_notification(db, {
                "user_id": t.owner_id,
                "title": "Task Deadline Approaching",
                "message": f"Reminder: Your task '{t.title}' is due on {t.due_date.strftime('%d %b %Y')}.",
                "notification_type": "DEADLINE",
                "channel": "DASHBOARD"
            })
        except Exception as e:
            logger.error(f"Error sending deadline reminder for task {t.id}: {e}")

    await db.commit()
    logger.info(f"Dispatched {len(due_tasks)} task deadline reminders.")

async def run_reminders_cron():
    """APScheduler မှ ခေါ်ယူရန် Wrapper Function"""
    async with AsyncSessionLocal() as db:
        try:
            await check_and_send_task_reminders(db)
        except Exception as e:
            logger.error(f"Error in reminders cron job: {e}")
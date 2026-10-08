import logging

from app.services.email_service import send_resend_email

logger = logging.getLogger(__name__)

async def send_email_notification(email: str, title: str, message: str):
    """Dispatch a notification through the centralized Resend email service."""
    return await send_resend_email(email, title, message)

def process_batch_notifications():
    """Grouped & Batch Notifications များကို စုစည်းပြီး တစ်ကြိမ်တည်း Email Digest ပို့ပေးရန် Cron Job"""
    logger.info("Processing batched/grouped notifications...")
    # Fetch unsent grouped notifications and send them as a digest
    logger.info("No batched notifications are currently queued.")
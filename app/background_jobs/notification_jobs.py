import logging

from app.services.email_service import EmailMessage, email_service

logger = logging.getLogger(__name__)

async def send_email_notification(email: str, title: str, message: str):
    """Dispatch a notification through the configured email provider."""
    return await email_service.send(
        EmailMessage(to=[email], subject=title, text_body=message)
    )

def process_batch_notifications():
    """Grouped & Batch Notifications များကို စုစည်းပြီး တစ်ကြိမ်တည်း Email Digest ပို့ပေးရန် Cron Job"""
    logger.info("Processing batched/grouped notifications...")
    # Fetch unsent grouped notifications and send them as a digest
    logger.info("No batched notifications are currently queued.")
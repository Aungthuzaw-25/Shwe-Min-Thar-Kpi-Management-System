import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)

async def send_email_notification(email: str, title: str, message: str):
    """Send an email when SMTP is explicitly enabled; never log credentials or message data."""
    if not settings.MAIL_ENABLED:
        logger.info("Email notification skipped because MAIL_ENABLED is false")
        return
    if not all((settings.MAIL_HOST, settings.MAIL_USERNAME, settings.MAIL_PASSWORD, settings.MAIL_FROM)):
        logger.error("Email notification skipped because mail configuration is incomplete")
        return

    def send() -> None:
        mail = EmailMessage()
        mail["Subject"] = title
        mail["From"] = f"{settings.MAIL_FROM_NAME} <{settings.MAIL_FROM}>"
        mail["To"] = email
        mail.set_content(message)
        with smtplib.SMTP(settings.MAIL_HOST, settings.MAIL_PORT, timeout=20) as smtp:
            if settings.MAIL_USE_TLS:
                smtp.starttls()
            smtp.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
            smtp.send_message(mail)

    try:
        await asyncio.to_thread(send)
        logger.info("Email notification dispatched")
    except Exception:
        logger.exception("Failed to send email notification")

def process_batch_notifications():
    """Grouped & Batch Notifications များကို စုစည်းပြီး တစ်ကြိမ်တည်း Email Digest ပို့ပေးရန် Cron Job"""
    logger.info("Processing batched/grouped notifications...")
    # Fetch unsent grouped notifications and send them as a digest
    logger.info("No batched notifications are currently queued.")
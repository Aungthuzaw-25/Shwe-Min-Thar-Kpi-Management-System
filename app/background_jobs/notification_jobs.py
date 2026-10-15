import logging

from app.services.email_service import EmailMessage, email_service

logger = logging.getLogger(__name__)

async def send_email_notification(email: str, title: str, message: str):
    """Dispatch a notification through the configured email provider."""
    return await email_service.send(
        EmailMessage(to=[email], subject=title, text_body=message)
    )

def process_batch_notifications():
    """Placeholder for batch delivery; no notification delivery is implemented."""
    logger.info(
        "Batch notification job is a no-op; batch notification delivery is not implemented."
    )
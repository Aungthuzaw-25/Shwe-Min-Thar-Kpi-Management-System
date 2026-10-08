import logging
from dataclasses import dataclass
from email.utils import formataddr

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.connection import AsyncSessionLocal
from app.models.system import SystemSetting
from app.modules.system.schemas import EmailConfigurationValue

logger = logging.getLogger(__name__)

RESEND_API_BASE_URL = "https://api.resend.com"
RESEND_TIMEOUT_SECONDS = 10.0
DEFAULT_FROM_NAME = "Shwe Min Thar KPI Management System"


@dataclass(frozen=True)
class EmailConfiguration:
    from_email: str | None
    from_name: str
    support_email: str | None


def email_configuration_from_value(value: dict | None) -> EmailConfiguration:
    validated = EmailConfigurationValue.model_validate(value or {})
    return EmailConfiguration(
        from_email=str(validated.from_email) if validated.from_email else None,
        from_name=validated.from_name,
        support_email=str(validated.support_email) if validated.support_email else None,
    )


async def get_email_configuration(db: AsyncSession) -> EmailConfiguration:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "email"))
    setting = result.scalar_one_or_none()
    return email_configuration_from_value(setting.value if setting else None)


async def _load_email_configuration() -> EmailConfiguration:
    async with AsyncSessionLocal() as db:
        return await get_email_configuration(db)


def _email_text_with_support(
    text: str, support_email: str | None, from_name: str
) -> str:
    if not support_email:
        return text
    return (
        f"{text}\n\n"
        "----------------------------------------\n"
        f"{from_name}\n\n"
        f"Need help? Contact {support_email}\n"
        "----------------------------------------"
    )


async def send_resend_email(to: str, subject: str, text: str) -> bool:
    api_key = (settings.RESEND_API_KEY or "").strip()
    if not api_key:
        logger.warning("Email notification skipped because RESEND_API_KEY is not configured")
        return False

    try:
        configuration = await _load_email_configuration()
    except ValidationError:
        logger.warning("Email notification skipped because the saved configuration is invalid")
        return False
    except SQLAlchemyError:
        logger.warning("Email notification skipped because saved configuration is unavailable")
        return False
    if not configuration.from_email:
        logger.warning("Email notification skipped because the sender address is not configured")
        return False

    payload = {
        "from": formataddr((configuration.from_name, configuration.from_email)),
        "to": [to],
        "subject": subject,
        "text": _email_text_with_support(
            text, configuration.support_email, configuration.from_name
        ),
    }
    try:
        async with httpx.AsyncClient(timeout=RESEND_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{RESEND_API_BASE_URL}/emails",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            response.raise_for_status()
    except httpx.TimeoutException:
        logger.warning("Resend email request timed out")
        return False
    except httpx.HTTPError:
        logger.warning("Resend email request failed")
        return False

    logger.info("Email notification dispatched through Resend")
    return True


async def test_resend_connection(
    configuration: EmailConfiguration,
) -> tuple[bool, str]:
    api_key = (settings.RESEND_API_KEY or "").strip()
    if not api_key:
        return False, "Resend API key is not configured."
    if not configuration.from_email:
        return False, "From Email is not configured."

    sender_domain = configuration.from_email.rsplit("@", maxsplit=1)[-1].lower()
    try:
        async with httpx.AsyncClient(timeout=RESEND_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{RESEND_API_BASE_URL}/domains",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            response_body = response.json()
            if not isinstance(response_body, dict) or not isinstance(
                response_body.get("data"), list
            ):
                return False, "Resend could not verify the configured sender."
            domains = response_body["data"]
    except httpx.TimeoutException:
        return False, "Resend connection timed out."
    except (httpx.HTTPError, ValueError, AttributeError):
        return False, "Resend could not verify the configured sender."

    if not any(
        isinstance(domain, dict)
        and str(domain.get("name", "")).lower() == sender_domain
        and domain.get("status") == "verified"
        for domain in domains
    ):
        return False, "The From Email domain is not verified with Resend."
    return True, "Resend is connected and the sender domain is verified."

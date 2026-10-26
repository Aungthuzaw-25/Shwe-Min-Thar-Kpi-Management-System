"""Over-achievement notices: score stays capped, humans get told.

When a finalized calculation finds a TARGET category whose uncapped
ratio exceeds 1, every active Super Admin + Admin gets one DASHBOARD
notice (never email) for that period + employee + category. The
notice is sent once per triple: a later recalculation of the same
period re-checks the existing notification rows (group_key) and
skips what was already sent.
"""

import hashlib
import logging
from uuid import UUID

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.kpi.engine_helpers import standardize_key
from app.modules.notifications.models import Notification
from app.modules.notifications.service import NotificationService
from app.modules.users.models import Role, User

logger = logging.getLogger(__name__)

NOTIFICATION_TYPE = "OVERACHIEVEMENT"
RECIPIENT_ROLE_NAMES = ("Super Admin", "Admin")


def _fmt_number(value: float) -> str:
    return f"{float(value):g}"


def build_overachievement_message(employee_name: str, entry: dict) -> str:
    """Burmese message carrying every field the dispatch requires,
    plus the one line that the excess is NOT in the score."""
    category = entry.get("category", "")
    target = _fmt_number(entry.get("target_value"))
    actual = _fmt_number(entry.get("actual_value"))
    pct = _fmt_number(entry.get("exceeded_pct"))
    message = (
        f"{employee_name} ရဲ့ {category} မှာ target {target} ကို "
        f"actual {actual} နဲ့ ကျော်သွားတယ် ({pct}% ပို)။ "
        "Score ထဲမှာ ပိုတာ မပါဘူး — bonus က လူကိုယ်တိုင် ဆုံးဖြတ်ရမှာ။"
    )
    if entry.get("actual_zero"):
        message += " (မှတ်ချက်: actual က 0 ဖြစ်တယ်။)"
    return message


def overachievement_group_key(
    employee_id: UUID,
    period_type,
    period_start,
    std_category_name: str,
) -> str:
    """Dedup key for one period + employee + category, under the
    100-char group_key limit. The recipient check scopes it per
    admin, so the key itself need not carry the recipient. The
    short hash suffix keeps long categories that share a 20-char
    prefix from colliding."""
    period_label = getattr(period_type, "value", str(period_type))
    start_label = period_start.strftime("%Y%m%d%H%M")
    suffix = hashlib.sha1(std_category_name.encode()).hexdigest()[:6]
    key = (
        f"kpi-over:{employee_id}:{period_label}:{start_label}:"
        f"{std_category_name[:20]}:{suffix}"
    )
    return key[:100]


async def notify_overachievements(
    db: AsyncSession,
    *,
    employee_id: UUID,
    employee_name: str,
    period_type,
    period_start,
    exceedances: list[dict],
    notifier=NotificationService.trigger_notification,
) -> list[dict]:
    """Send DASHBOARD notices for exceeded categories, once each.

    Returns the notices that were actually sent (empty when nothing
    exceeded, or when every notice already exists). Failures to
    notify one recipient never block the others; they are logged.
    """
    exceeded = [entry for entry in exceedances if entry.get("exceeded")]
    if not exceeded:
        return []
    recipients = (
        await db.execute(
            select(User)
            .join(Role, Role.id == User.role_id)
            .where(
                Role.name.in_(RECIPIENT_ROLE_NAMES),
                User.is_active.is_(True),
                User.is_deleted.is_(False),
            )
        )
    ).scalars().all()
    sent = []
    for entry in exceeded:
        group_key = overachievement_group_key(
            employee_id,
            period_type,
            period_start,
            standardize_key(entry.get("category", "")),
        )
        title = f"KPI target ကျော်သွားတယ် — {employee_name}"
        message = build_overachievement_message(employee_name, entry)
        for recipient in recipients:
            existing = (
                await db.execute(
                    select(Notification.id).where(
                        Notification.user_id == recipient.id,
                        Notification.group_key == group_key,
                        Notification.is_deleted.is_(False),
                    )
                )
            ).scalars().first()
            if existing is not None:
                continue
            try:
                await notifier(
                    user_id=recipient.id,
                    title=title,
                    message=message,
                    notification_type=NOTIFICATION_TYPE,
                    background_tasks=BackgroundTasks(),
                    channel="DASHBOARD",
                    group_key=group_key,
                )
                sent.append({"recipient_id": recipient.id, "group_key": group_key})
            except Exception:
                logger.exception(
                    "Failed to send over-achievement notice to %s", recipient.id
                )
    return sent


async def flush_pending_notifications(
    db: AsyncSession,
    pending: list[dict],
    *,
    notifier=NotificationService.trigger_notification,
) -> list[dict]:
    """Send notices deferred by commit=False calculations, after
    the owning transaction committed. Each pending item carries
    plain data (no ORM objects), so it survives the outer commit.
    A failure on one item is logged and never blocks the rest.
    """
    sent = []
    for item in pending:
        try:
            sent.extend(
                await notify_overachievements(
                    db,
                    employee_id=item["employee_id"],
                    employee_name=item["employee_name"],
                    period_type=item["period_type"],
                    period_start=item["period_start"],
                    exceedances=item["exceedances"],
                    notifier=notifier,
                )
            )
        except Exception:
            logger.exception("Failed to flush pending over-achievement notice")
    return sent

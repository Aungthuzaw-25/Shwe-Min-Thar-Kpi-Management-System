"""Informational notices for significant administrative actions."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.models import Notification
from app.modules.users.models import Role, User


async def stage_super_admin_notice(
    db: AsyncSession,
    *,
    actor_id: UUID,
    title: str,
    message: str,
) -> None:
    """Stage a dashboard notice for every other active Super Admin."""
    recipients = (
        await db.execute(
            select(User.id)
            .join(Role, User.role_id == Role.id)
            .where(
                Role.name == "Super Admin",
                User.is_active.is_(True),
                User.is_deleted.is_(False),
                User.id != actor_id,
            )
        )
    ).scalars().all()
    for recipient_id in recipients:
        db.add(
            Notification(
                user_id=recipient_id,
                title=title,
                message=message,
                notification_type="ADMIN_NOTICE",
                channel="DASHBOARD",
                group_key="super-admin-action",
            )
        )

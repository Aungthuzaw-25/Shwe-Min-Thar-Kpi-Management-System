"""Activity timeline persistence and scope helpers."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.departments.models import UserDepartment
from app.modules.tasks.models.activity_timeline import ActivityTimeline
from app.modules.users.models import User
from app.core.dependencies import GLOBAL_SCOPE_ROLES


async def record_activity(
    db: AsyncSession,
    *,
    actor_id: UUID,
    action: str,
    entity_type: str,
    entity_id: UUID,
    description: str,
) -> ActivityTimeline:
    """Stage an activity in the caller's transaction."""
    activity = ActivityTimeline(
        user_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
    )
    db.add(activity)
    return activity


async def get_activity_scope_user_ids(
    db: AsyncSession, current_user: User
) -> set[UUID] | None:
    """Return actor ids visible to the caller, or None for global roles."""
    role_name = getattr(getattr(current_user, "role", None), "name", "")
    if role_name in GLOBAL_SCOPE_ROLES or role_name in {"Department Head", "Admin"}:
        department_ids = (
            await db.execute(
                select(UserDepartment.department_id).where(
                    UserDepartment.user_id == current_user.id,
                    UserDepartment.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        if not department_ids:
            return {current_user.id}
        actor_ids = (
            await db.execute(
                select(UserDepartment.user_id).where(
                    UserDepartment.department_id.in_(department_ids),
                    UserDepartment.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        return set(actor_ids)

    level_rows = (
        await db.execute(
            select(UserDepartment.user_level).where(
                UserDepartment.user_id == current_user.id,
                UserDepartment.is_deleted.is_(False),
            )
        )
    ).scalars().all()
    levels = {str(level).upper().replace("LEVEL ", "L") for level in level_rows}
    if levels.intersection({"L0", "L1"}):
        department_ids = (
            await db.execute(
                select(UserDepartment.department_id).where(
                    UserDepartment.user_id == current_user.id,
                    UserDepartment.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        actor_ids = (
            await db.execute(
                select(UserDepartment.user_id).where(
                    UserDepartment.department_id.in_(department_ids),
                    UserDepartment.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        return set(actor_ids) | {current_user.id}
    return {current_user.id}


def activity_payload(activity: ActivityTimeline, actor: User | None) -> dict[str, Any]:
    return {
        "id": activity.id,
        "actor_id": activity.user_id,
        "actor_name": (
            f"{actor.first_name} {actor.last_name}".strip()
            if actor
            else "System"
        ),
        "action": activity.action,
        "entity_type": activity.entity_type,
        "entity_id": activity.entity_id,
        "description": activity.description,
        "created_at": activity.created_at,
    }

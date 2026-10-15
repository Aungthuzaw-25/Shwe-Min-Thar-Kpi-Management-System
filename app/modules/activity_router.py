from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.dependencies import get_current_user
from app.database.connection import get_db
from app.modules.departments.models import UserDepartment
from app.modules.tasks.models.activity_timeline import ActivityTimeline
from app.services.activity_service import activity_payload, get_activity_scope_user_ids

router = APIRouter(prefix="/activity", tags=["Activity"])


async def _list_activity(
    db: AsyncSession,
    current_user,
    skip: int,
    limit: int,
    actor_id: UUID | None = None,
    department_id: UUID | None = None,
) -> list[dict[str, Any]]:
    scope_ids = await get_activity_scope_user_ids(db, current_user)
    if actor_id is not None:
        if scope_ids is not None and actor_id not in scope_ids:
            raise HTTPException(status_code=403, detail="Activity scope denied")
        scope_ids = {actor_id}
    if department_id is not None:
        department_actor_ids = set(
            (
                await db.execute(
                    select(UserDepartment.user_id).where(
                        UserDepartment.department_id == department_id,
                        UserDepartment.is_deleted.is_(False),
                    )
                )
            ).scalars().all()
        )
        if scope_ids is None:
            scope_ids = department_actor_ids
        else:
            scope_ids &= department_actor_ids
    stmt = (
        select(ActivityTimeline)
        .options(joinedload(ActivityTimeline.user))
        .where(ActivityTimeline.is_deleted.is_(False))
        .order_by(ActivityTimeline.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    if scope_ids is not None:
        stmt = stmt.where(ActivityTimeline.user_id.in_(scope_ids))
    activities = (await db.execute(stmt)).scalars().all()
    return [activity_payload(item, item.user) for item in activities]


@router.get("", responses={403: {"description": "Activity scope denied"}})
async def get_activity(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=100),
    actor_id: UUID | None = Query(None),
    department_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return await _list_activity(db, current_user, skip, limit, actor_id, department_id)


@router.get("/recent", responses={403: {"description": "Activity scope denied"}})
async def get_recent_activity(
    limit: int = Query(10, gt=0, le=100),
    actor_id: UUID | None = Query(None),
    department_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return await _list_activity(db, current_user, 0, limit, actor_id, department_id)

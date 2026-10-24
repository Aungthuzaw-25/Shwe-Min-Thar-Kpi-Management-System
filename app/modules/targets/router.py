from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload

from app.core.dependencies import (
    GLOBAL_SCOPE_ROLES,
    RoleChecker,
    enforce_department_scope,
    enforce_permission,
    get_current_user,
)
from app.core.idempotency import idempotency_guard
from app.database.connection import get_db
from app.modules.departments.models import Department, UserDepartment
from app.modules.targets.models import Target, TargetStatus
from app.modules.targets.schemas import TargetCreate, TargetResponse
from app.modules.users.models import User

router = APIRouter()
admin_checker = RoleChecker(['Super Admin', 'Admin'])

@router.post(
    "/",
    dependencies=[Depends(idempotency_guard)],
    response_model=TargetResponse,
    status_code=status.HTTP_201_CREATED,
    responses={500: {"description": "Failed to assign target"}},
)
async def create_target(
    payload: TargetCreate, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(admin_checker)
):
    enforce_permission(current_user, "kpi.edit")
    try:
        if payload.department_id:
            await enforce_department_scope(db, current_user, payload.department_id)
        new_target = Target(
            name=payload.name,
            department_id=payload.department_id,
            target_value=payload.target_value,
            metric_type=payload.metric_type,
            target_month=payload.target_month,
            created_by_id=current_user.id
        )
        db.add(new_target)
        await db.commit()
        await db.refresh(new_target)

        # Get department name for immediate UI response
        dept_name = None
        if new_target.department_id:
            dept = await db.get(Department, new_target.department_id)
            if dept: 
                dept_name = dept.name

        response_data = TargetResponse.model_validate(new_target)
        response_data.department_name = dept_name
        return response_data
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to assign target: {str(e)}")

# 🌟 PERFORMANCE FIX: Added skip and limit for Pagination
@router.get("/", response_model=list[TargetResponse])
async def get_targets(
    status_filter: str | None = Query(None, alias="status"),
    skip: int = Query(0, ge=0), # 🌟 Pagination
    limit: int = Query(100, gt=0, le=500), # 🌟 Pagination
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, "kpi.view")
    # 🌟 DATA INTEGRITY FIX: Filter out soft-deleted targets
    stmt = select(Target).options(joinedload(Target.department)).where(Target.is_deleted == False).order_by(Target.created_at.desc())
    if not current_user.role or current_user.role.name not in GLOBAL_SCOPE_ROLES:
        stmt = stmt.where(
            (Target.department_id.is_(None))
            | Target.department_id.in_(
                select(UserDepartment.department_id).where(
                    UserDepartment.user_id == current_user.id
                )
            )
        )

    if status_filter and status_filter != "ALL":
        try:
            stmt = stmt.where(Target.status == TargetStatus[status_filter.upper()])
        except KeyError:
            pass # Ignore invalid status filters silently to preserve UX state

    # Apply Pagination
    stmt = stmt.offset(skip).limit(limit)

    result = await db.execute(stmt)
    targets = result.scalars().all()

    response_list = []
    for t in targets:
        resp = TargetResponse.model_validate(t)
        resp.department_name = t.department.name if t.department else None
        response_list.append(resp)
        
    return response_list

@router.delete(
    "/{target_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"description": "Target not found"},
        500: {"description": "Cannot delete target"},
    },
)
async def delete_target(
    target_id: UUID, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(admin_checker)
):
    enforce_permission(current_user, "kpi.edit")
    try:
        # 🌟 FIX: Fetch only active target using select
        stmt = select(Target).where(Target.id == target_id, Target.is_deleted == False)
        target = (await db.execute(stmt)).scalars().first()
        
        if not target:
            raise HTTPException(status_code=404, detail="Target not found.")
        if target.department_id:
            await enforce_department_scope(db, current_user, target.department_id)
            
        # 🌟 DATA INTEGRITY FIX: Changed from db.delete() to Soft Delete 
        # to preserve historical reports and database integrity.
        target.is_deleted = True
        db.add(target)
        await db.commit()
        return None
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Cannot delete target due to internal dependencies: {str(e)}")
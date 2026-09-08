from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload
from typing import List, Optional
from uuid import UUID

from app.database.connection import get_db
from app.core.dependencies import get_current_user, RoleChecker
from app.modules.users.models import User
from app.modules.departments.models import Department
from app.modules.targets.models import Target, TargetStatus
from app.modules.targets.schemas import TargetCreate, TargetResponse

router = APIRouter()
admin_checker = RoleChecker(['Super Admin', 'Admin'])

@router.post("/", response_model=TargetResponse, status_code=status.HTTP_201_CREATED)
async def create_target(
    payload: TargetCreate, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(admin_checker)
):
    try:
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
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to assign target: {str(e)}")

# 🌟 PERFORMANCE FIX: Added skip and limit for Pagination
@router.get("/", response_model=List[TargetResponse])
async def get_targets(
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0), # 🌟 Pagination
    limit: int = Query(100, gt=0, le=500), # 🌟 Pagination
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # 🌟 DATA INTEGRITY FIX: Filter out soft-deleted targets
    stmt = select(Target).options(joinedload(Target.department)).where(Target.is_deleted == False).order_by(Target.created_at.desc())

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

@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target(
    target_id: UUID, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(admin_checker)
):
    try:
        # 🌟 FIX: Fetch only active target using select
        stmt = select(Target).where(Target.id == target_id, Target.is_deleted == False)
        target = (await db.execute(stmt)).scalars().first()
        
        if not target:
            raise HTTPException(status_code=404, detail="Target not found.")
            
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
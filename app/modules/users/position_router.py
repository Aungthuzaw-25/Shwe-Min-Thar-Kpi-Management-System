from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

# E2: positions are gated by permission keys (department.edit /
# department.view), not by role names.
from app.core.dependencies import (
    enforce_department_scope,
    enforce_permission,
    get_current_user,
)
from app.database.connection import get_db
from app.modules.users import position_repo
from app.modules.users.models import User
from app.modules.users.position_schemas import PositionCreate, PositionResponse, PositionUpdate

router = APIRouter(prefix="/positions", tags=["Positions"])

DEPARTMENT_EDIT_PERMISSION = "department.edit"
POSITION_NOT_FOUND = "Position not found"

@router.post("/", response_model=PositionResponse, status_code=status.HTTP_201_CREATED, responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def create_new_position(
    position_in: PositionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce_permission(current_user, DEPARTMENT_EDIT_PERMISSION)
    await enforce_department_scope(db, current_user, position_in.department_id)
    return await position_repo.create_position(db, position_in)

@router.get("/department/{department_id}", response_model=list[PositionResponse], responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def read_positions_by_department(
    department_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user) # 🌟 get_current_user သို့ ပြောင်းထားပါသည်
):
    await enforce_department_scope(db, current_user, department_id)
    return await position_repo.get_positions_by_department(db, department_id)

@router.get("/{position_id}", response_model=PositionResponse, responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def read_position(
    position_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user) # 🌟 get_current_user သို့ ပြောင်းထားပါသည်
):
    enforce_permission(current_user, "department.view")
    position = await position_repo.get_position(db, position_id)
    if not position:
        raise HTTPException(status_code=404, detail=POSITION_NOT_FOUND)
    await enforce_department_scope(db, current_user, position.department_id)
    return position

@router.put("/{position_id}", response_model=PositionResponse, responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def update_existing_position(
    position_id: UUID,
    position_in: PositionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce_permission(current_user, DEPARTMENT_EDIT_PERMISSION)
    position = await position_repo.get_position(db, position_id)
    if not position:
        raise HTTPException(status_code=404, detail=POSITION_NOT_FOUND)
    await enforce_department_scope(db, current_user, position.department_id)
    updated = await position_repo.update_position(db, position_id, position_in)
    if not updated:
        raise HTTPException(status_code=404, detail=POSITION_NOT_FOUND)
    return updated

@router.delete("/{position_id}", status_code=status.HTTP_204_NO_CONTENT, responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def delete_existing_position(
    position_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce_permission(current_user, DEPARTMENT_EDIT_PERMISSION)
    position = await position_repo.get_position(db, position_id)
    if not position:
        raise HTTPException(status_code=404, detail=POSITION_NOT_FOUND)
    await enforce_department_scope(db, current_user, position.department_id)
    success = await position_repo.delete_position(db, position_id)
    if not success:
        raise HTTPException(status_code=404, detail=POSITION_NOT_FOUND)
    return None
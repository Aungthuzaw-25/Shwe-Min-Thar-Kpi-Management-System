from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from uuid import UUID

from app.database.connection import get_db
# 🌟 ပြင်ဆင်ချက်: get_current_user နှင့် RoleChecker ကို ပြောင်းလဲအသုံးပြုထားပါသည်
from app.core.dependencies import get_current_user, RoleChecker
from app.modules.users import position_repo
from app.modules.users.position_schemas import PositionCreate, PositionUpdate, PositionResponse
from app.modules.users.models import User

router = APIRouter(prefix="/positions", tags=["Positions"])

# 🌟 ပြင်ဆင်ချက်: RoleChecker သို့ ပြောင်းထားပါသည်
admin_only = RoleChecker(["Super Admin", "Admin", "HR"])

@router.post("/", response_model=PositionResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(admin_only)])
async def create_new_position(
    position_in: PositionCreate,
    db: AsyncSession = Depends(get_db)
):
    return await position_repo.create_position(db, position_in)

@router.get("/department/{department_id}", response_model=List[PositionResponse])
async def read_positions_by_department(
    department_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user) # 🌟 get_current_user သို့ ပြောင်းထားပါသည်
):
    return await position_repo.get_positions_by_department(db, department_id)

@router.get("/{position_id}", response_model=PositionResponse)
async def read_position(
    position_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user) # 🌟 get_current_user သို့ ပြောင်းထားပါသည်
):
    position = await position_repo.get_position(db, position_id)
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    return position

@router.put("/{position_id}", response_model=PositionResponse, dependencies=[Depends(admin_only)])
async def update_existing_position(
    position_id: UUID,
    position_in: PositionUpdate,
    db: AsyncSession = Depends(get_db)
):
    updated = await position_repo.update_position(db, position_id, position_in)
    if not updated:
        raise HTTPException(status_code=404, detail="Position not found")
    return updated

@router.delete("/{position_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(admin_only)])
async def delete_existing_position(
    position_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    success = await position_repo.delete_position(db, position_id)
    if not success:
        raise HTTPException(status_code=404, detail="Position not found")
    return None
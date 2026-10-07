from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.modules.users.models import Position
from app.modules.users.position_schemas import PositionCreate, PositionUpdate


async def create_position(db: AsyncSession, obj_in: PositionCreate) -> Position:
    db_obj = Position(**obj_in.dict())
    db.add(db_obj)
    await db.commit()
    await db.refresh(db_obj)
    return db_obj

async def get_position(db: AsyncSession, position_id: UUID) -> Position | None:
    stmt = select(Position).where(Position.id == position_id, Position.is_deleted == False)
    result = await db.execute(stmt)
    return result.scalars().first()

async def get_positions_by_department(db: AsyncSession, department_id: UUID) -> list[Position]:
    stmt = select(Position).where(Position.department_id == department_id, Position.is_deleted == False)
    result = await db.execute(stmt)
    return result.scalars().all()

async def update_position(db: AsyncSession, position_id: UUID, obj_in: PositionUpdate) -> Position | None:
    db_obj = await get_position(db, position_id)
    if not db_obj:
        return None
        
    update_data = obj_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_obj, field, value)
        
    await db.commit()
    await db.refresh(db_obj)
    return db_obj

async def delete_position(db: AsyncSession, position_id: UUID) -> bool:
    db_obj = await get_position(db, position_id)
    if not db_obj:
        return False
        
    db_obj.is_deleted = True
    await db.commit()
    return True
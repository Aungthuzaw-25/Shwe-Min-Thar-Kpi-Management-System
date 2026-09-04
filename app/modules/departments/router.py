from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload  
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func

from app.database.connection import get_db
from app.modules.departments.schemas import DepartmentCreate, DepartmentUpdate, DepartmentResponse, AssignUserRequest, PositionInDepartment
from app.modules.departments import repo as department_repo
from app.core.dependencies import RoleChecker, get_current_user, enforce_permission, enforce_department_scope
from app.modules.users.models import User
from app.modules.departments.models import Department, UserDepartment
from app.modules.users.models import Position  
from app.modules.tasks.models.task import Task
from app.modules.tasks.models.task_history import TaskHistory

router = APIRouter()

admin_checker = RoleChecker(['Super Admin', 'System Super Admin', 'Admin'])

class ChangeLevelRequest(BaseModel):
    user_level: str

class TransferUserRequest(BaseModel):
    user_id: UUID
    old_department_id: UUID
    new_department_id: UUID

class DepartmentPriorityRequest(BaseModel):
    priority: int

@router.post("/", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
async def create_department(payload: DepartmentCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(admin_checker)):
    enforce_permission(current_user, "department.create")
    existing_dept = await department_repo.get_department_by_name(db, payload.name)
    if existing_dept:
        raise HTTPException(status_code=400, detail="This department name is already in use.")
        
    try:
        return await department_repo.create_department(db, payload.model_dump())
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Invalid data provided for department creation.")

# 🌟 PERFORMANCE FIX: Added skip and limit for pagination to prevent memory overload
@router.get("/", response_model=List[DepartmentResponse])
async def get_departments(
    is_active: Optional[bool] = Query(True), 
    skip: int = Query(0, ge=0),
    limit: int = Query(100, gt=0),
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, "department.view")
    if is_active:
        stmt = select(Department).where(Department.is_deleted == False).order_by(Department.priority.desc(), Department.name, Department.id).offset(skip).limit(limit)
    else:
        stmt = select(Department).where(Department.is_deleted == True).order_by(Department.priority.desc(), Department.name, Department.id).offset(skip).limit(limit)
        
    result = await db.execute(stmt)
    return result.scalars().all()

@router.get("/{dept_id}", response_model=DepartmentResponse)
async def get_department(dept_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, "department.view")
    dept = await department_repo.get_department_by_id(db, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
    return dept

@router.get("/{dept_id}/positions", response_model=List[PositionInDepartment])
async def get_department_positions(dept_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, "department.view")
    dept = await department_repo.get_department_by_id(db, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
    
    stmt = select(Position).where(Position.department_id == dept_id)
    result = await db.execute(stmt)
    return result.scalars().all()

@router.put("/{dept_id}", response_model=DepartmentResponse)
async def update_department(dept_id: UUID, payload: DepartmentUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(admin_checker)):
    enforce_permission(current_user, "department.edit")
    dept = await department_repo.get_department_by_id(db, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
        
    if payload.name and payload.name != dept.name:
        existing_dept = await department_repo.get_department_by_name(db, payload.name)
        if existing_dept:
            raise HTTPException(status_code=400, detail="This department name is already in use.")
            
    try:
        update_data = payload.model_dump(exclude_unset=True)
        return await department_repo.update_department(db, dept, update_data)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Invalid data provided for department update.")

@router.patch("/{dept_id}/priority", response_model=DepartmentResponse)
async def update_department_priority(
    dept_id: UUID,
    payload: DepartmentPriorityRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_checker),
):
    enforce_permission(current_user, "department.edit")
    dept = await department_repo.get_department_by_id(db, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
    if payload.priority < 0 or payload.priority > 100:
        raise HTTPException(status_code=422, detail="Priority must be between 0 and 100.")
    dept.priority = payload.priority
    await db.commit()
    await db.refresh(dept)
    return dept

@router.delete("/{dept_id}")
async def delete_department(dept_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(admin_checker)):
    enforce_permission(current_user, "department.delete")
    dept = await department_repo.get_department_by_id(db, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
        
    # 🌟 REFACTOR FIX: Using Repo function instead of direct query for better architecture
    has_users = await department_repo.check_users_in_dept(db, dept_id)
    if has_users:
        raise HTTPException(
            status_code=400, 
            detail="Cannot delete department. Users are still assigned to this department. Please transfer or remove them first."
        )
        
    await department_repo.delete_department(db, dept)
    return {"message": "Department deactivated successfully (Soft Delete)"}

@router.post("/{dept_id}/restore", response_model=DepartmentResponse)
async def restore_department(dept_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(admin_checker)):
    enforce_permission(current_user, "department.edit")
    dept = await department_repo.get_department_any(db, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
        
    if not dept.is_deleted:
        raise HTTPException(status_code=400, detail="Department is not deleted.")
        
    return await department_repo.restore_department(db, dept)

@router.delete("/{dept_id}/permanent")
async def permanent_delete_department(dept_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(admin_checker)):
    enforce_permission(current_user, "department.delete")
    dept = await department_repo.get_department_any(db, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
        
    # 🌟 REFACTOR FIX: Using Repo function instead of direct query
    has_users = await department_repo.check_users_in_dept(db, dept_id)
    if has_users:
        raise HTTPException(
            status_code=400, 
            detail="Cannot permanently delete department. Users are still assigned to this department."
        )
        
    try:
        await department_repo.hard_delete_department(db, dept)
        return {"message": "Department permanently deleted successfully (Hard Delete)"}
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=400, 
            detail="Cannot permanently delete this department because it is actively linked to existing KPI formulas, settings, or historical reports. Please rely on 'Trash (Soft Delete)' to preserve system history."
        )
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{dept_id}/users")
async def assign_user_to_department(
    dept_id: UUID, 
    payload: AssignUserRequest, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(admin_checker)
):
    dept = await department_repo.get_department_by_id(db, dept_id)
    if not dept: raise HTTPException(status_code=404, detail="Department not found.")
    
    target_user = (await db.execute(select(User).where(User.id == payload.user_id, User.is_deleted == False))).scalars().first()
    if not target_user: raise HTTPException(status_code=404, detail="User not found.")
    
    existing_assignment = (await db.execute(select(UserDepartment).where(UserDepartment.user_id == payload.user_id, UserDepartment.department_id == dept_id))).scalars().first()
    if existing_assignment:
        raise HTTPException(status_code=400, detail="User is already assigned to this department.")

    try:
        new_assignment = UserDepartment(user_id=payload.user_id, department_id=dept_id, user_level=payload.user_level)
        db.add(new_assignment)
        
        # 🌟 SECURITY FIX: Prevent Ghost L0 Heads by demoting previous head
        if payload.user_level == "L0":
            if dept.head_id and str(dept.head_id) != str(payload.user_id):
                old_head_ud = (await db.execute(select(UserDepartment).where(UserDepartment.department_id == dept_id, UserDepartment.user_id == dept.head_id))).scalars().first()
                if old_head_ud:
                    old_head_ud.user_level = "L1" # Demote old head to L1
                    db.add(old_head_ud)
            
            dept.head_id = target_user.id
            message = "User successfully assigned as Department Head (L0)."
        else:
            message = f"User successfully assigned to the department at Level {payload.user_level}."
            
        db.add(dept)
        await db.commit()
        return {"message": message}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {str(e)}")

@router.get("/{dept_id}/users")
async def get_department_users(dept_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    await enforce_department_scope(db, current_user, dept_id)
    dept = await department_repo.get_department_by_id(db, dept_id)
    if not dept: raise HTTPException(status_code=404, detail="Department not found.")
    
    stmt = select(UserDepartment, User).join(User).where(
        UserDepartment.department_id == dept_id, 
        User.is_deleted == False
    ).options(
        selectinload(User.position)
    ).order_by(UserDepartment.user_level)
    
    result = await db.execute(stmt)
    rows = result.all()
    
    return [{
        "id": user.id,
        "name": f"{user.first_name} {user.last_name}",
        "email": user.email,
        "position": user.position.name if getattr(user, 'position', None) else "No Title",
        "region_town": user.region_town,
        "user_level": ud.user_level,
        "is_department_head": str(user.id) == str(dept.head_id) if dept.head_id else False
    } for ud, user in rows]

@router.delete("/{dept_id}/users/{user_id}")
async def remove_user_from_department(dept_id: UUID, user_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(admin_checker)):
    await enforce_department_scope(db, current_user, dept_id)
    ud = (await db.execute(select(UserDepartment).where(UserDepartment.department_id == dept_id, UserDepartment.user_id == user_id))).scalars().first()
    if not ud: raise HTTPException(status_code=404, detail="User is not found in this department.")
    
    try:
        dept = await department_repo.get_department_by_id(db, dept_id)
        if dept and str(dept.head_id) == str(user_id):
            dept.head_id = None
            db.add(dept)
            
        await db.delete(ud)
        await db.commit()
        return {"message": "User removed from department successfully."}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {str(e)}")

@router.put("/{dept_id}/users/{user_id}/level")
async def change_user_level(dept_id: UUID, user_id: UUID, payload: ChangeLevelRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(admin_checker)):
    await enforce_department_scope(db, current_user, dept_id)
    ud = (await db.execute(select(UserDepartment).where(UserDepartment.department_id == dept_id, UserDepartment.user_id == user_id))).scalars().first()
    if not ud: raise HTTPException(status_code=404, detail="User is not found in this department.")
    
    try:
        ud.user_level = payload.user_level
        db.add(ud)
        
        dept = await department_repo.get_department_by_id(db, dept_id)
        
        # 🌟 SECURITY FIX: Handled L0 demotion to prevent ghost heads
        if payload.user_level == "L0":
            if dept.head_id and str(dept.head_id) != str(user_id):
                old_head_ud = (await db.execute(select(UserDepartment).where(UserDepartment.department_id == dept_id, UserDepartment.user_id == dept.head_id))).scalars().first()
                if old_head_ud:
                    old_head_ud.user_level = "L1"
                    db.add(old_head_ud)
            dept.head_id = user_id
        elif str(dept.head_id) == str(user_id) and payload.user_level != "L0":
            dept.head_id = None
            
        db.add(dept)
        await db.commit()
        return {"message": "User level updated successfully."}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {str(e)}")

@router.put("/transfer-user")
async def transfer_user(payload: TransferUserRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(admin_checker)):
    await enforce_department_scope(db, current_user, payload.old_department_id)
    await enforce_department_scope(db, current_user, payload.new_department_id)
    old_ud = (await db.execute(select(UserDepartment).where(UserDepartment.department_id == payload.old_department_id, UserDepartment.user_id == payload.user_id))).scalars().first()
    if not old_ud: raise HTTPException(status_code=404, detail="User is not found in the old department.")
    
    existing_new_ud = (await db.execute(select(UserDepartment).where(UserDepartment.department_id == payload.new_department_id, UserDepartment.user_id == payload.user_id))).scalars().first()
    if existing_new_ud:
        raise HTTPException(status_code=400, detail="User is already assigned to the target department.")
        
    try:
        old_dept = await department_repo.get_department_by_id(db, payload.old_department_id)
        if old_dept and str(old_dept.head_id) == str(payload.user_id):
            old_dept.head_id = None
            db.add(old_dept)
            
        new_ud = UserDepartment(user_id=payload.user_id, department_id=payload.new_department_id, user_level=old_ud.user_level)
        db.add(new_ud)
        await db.delete(old_ud)
        
        await db.commit()
        return {"message": "User transferred to the new department successfully."}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {str(e)}")
    

# ==============================================================================
# 🌟 SUPER ADMIN: Department Process Tracking API
# ==============================================================================
@router.get("/{dept_id}/process-tracking", tags=["Super Admin Tracking"])
async def get_department_process_tracking(
    dept_id: UUID, 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(admin_checker)
):
    await enforce_department_scope(db, current_user, dept_id)
    """ဌာနတစ်ခုလုံး၏ Process များ၊ တစ်ဆို့နေမှုများနှင့် Activity Timeline ကို Tracking လိုက်ရန်"""
    
    # 1. ဌာန ရှိ/မရှိ စစ်ဆေးခြင်း
    dept_check = await db.get(Department, dept_id)
    if not dept_check:
        raise HTTPException(status_code=404, detail="Department not found.")

    # 2. ထိုဌာနအတွင်းရှိ User များ၏ Process Status များကို ရေတွက်ခြင်း (Metrics)
    metrics_stmt = (
        select(Task.status, func.count(Task.id))
        .join(User, Task.owner_id == User.id)
        .join(UserDepartment, User.id == UserDepartment.user_id)
        .where(UserDepartment.department_id == dept_id)
        .group_by(Task.status)
    )
    metrics_req = await db.execute(metrics_stmt)
    
    process_metrics = {
        "DRAFT": 0, "PENDING_L1_REVIEW": 0, "PENDING_L0_REVIEW": 0, "COMPLETED": 0, "REJECTED": 0
    }
    
    for status_enum, count in metrics_req.all():
        status_val = status_enum.value if hasattr(status_enum, 'value') else str(status_enum)
        if "Draft" in status_val: process_metrics["DRAFT"] = count
        elif "L1" in status_val: process_metrics["PENDING_L1_REVIEW"] = count
        elif "L0" in status_val: process_metrics["PENDING_L0_REVIEW"] = count
        elif "Completed" in status_val or "Approved" in status_val: process_metrics["COMPLETED"] += count
        elif "Rejected" in status_val: process_metrics["REJECTED"] = count

    # 3. ဌာန၏ နောက်ဆုံး Process Timeline ဆွဲထုတ်ခြင်း
    timeline_stmt = (
        select(TaskHistory, Task.title, User.first_name, User.last_name)
        .join(Task, TaskHistory.task_id == Task.id)
        .join(UserDepartment, Task.owner_id == UserDepartment.user_id)
        .outerjoin(User, TaskHistory.changed_by == User.id)
        .where(UserDepartment.department_id == dept_id)
        .order_by(TaskHistory.created_at.desc())
        .limit(15)
    )
    timeline_req = await db.execute(timeline_stmt)

    recent_activities = []
    for history, task_title, f_name, l_name in timeline_req.all():
        actor_name = f"{f_name} {l_name}".strip() if f_name else "System"
        recent_activities.append({
            "task_title": task_title,
            "action_by": actor_name,
            "action": history.action,
            "remarks": history.remarks,
            "timestamp": history.created_at.isoformat()
        })

    return {
        "user_name": None, # Department Tracking ဖြစ်၍ User Name ကို Null ထားမည်
        "tracking_metrics": process_metrics,
        "recent_process_activities": recent_activities
    }
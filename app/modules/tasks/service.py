from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload, selectinload

from app.core.dependencies import GLOBAL_SCOPE_ROLES, enforce_permission
from app.core.timezone import APPLICATION_TIMEZONE
from app.modules.departments.models import UserDepartment
from app.modules.kpi.engine import trigger_kpi_recalculation

# 🌟 FIX: Updated import to use the refactored module-level function
from app.modules.notifications.repo import create_notification
from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.tasks.models.task_review import TaskReview
from app.modules.tasks.schemas import TaskCreate, TaskReviewInput
from app.modules.users.models import Role, User, UserPermissionOverride
from app.services.activity_service import record_activity

timezone = APPLICATION_TIMEZONE


async def _authorize_reviewer(
    db: AsyncSession,
    task: Task,
    current_user_id: UUID,
    required_level: str,
) -> None:
    result = await db.execute(
        select(User)
        .options(
            joinedload(User.role).selectinload(Role.permissions),
            joinedload(User.user_departments),
            selectinload(User.permission_overrides).selectinload(
                UserPermissionOverride.permission
            ),
        )
        .where(User.id == current_user_id, User.is_deleted.is_(False), User.is_active.is_(True))
    )
    current_user = result.unique().scalars().first()
    if not current_user:
        raise HTTPException(status_code=403, detail="Reviewer is not active.")
    enforce_permission(current_user, "tasks.approve")
    if current_user.role and current_user.role.name in GLOBAL_SCOPE_ROLES:
        return

    owner_department_ids = {
        str(link.department_id)
        for link in (getattr(task.owner, "user_departments", []) or [])
        if not getattr(link, "is_deleted", False)
    }
    reviewer_links = [
        link for link in (current_user.user_departments or []) if not link.is_deleted
    ]
    if not any(
        str(link.department_id) in owner_department_ids
        and str(link.user_level).upper().replace("LEVEL ", "") == required_level
        for link in reviewer_links
    ):
        raise HTTPException(status_code=403, detail="Reviewer is outside the task workflow scope.")

# ==============================================================================
# ၁။ Task အသစ်ဖန်တီးခြင်း နှင့် တင်ပြခြင်း (L2)
# ==============================================================================
async def create_task(db: AsyncSession, task_in: TaskCreate, current_user_id: UUID) -> Task:
    try:
        new_task = Task(
            title=task_in.title,
            description=task_in.description,
            category=task_in.category, 
            status=TaskStatus.DRAFT,
            due_date=task_in.due_date,
            owner_id=current_user_id
        )
        db.add(new_task)
        if hasattr(db, "flush"):
            await db.flush()
        record_activity(
            db,
            actor_id=current_user_id,
            action="TASK_CREATED",
            entity_type="task",
            entity_id=new_task.id,
            description=f"Created task '{new_task.title}'",
        )
        await db.commit()
        await db.refresh(new_task)
        return new_task
    except Exception as e:
        await db.rollback()
        raise e

async def submit_task(db: AsyncSession, task_id: UUID, current_user_id: UUID) -> Task:
    stmt = (
        select(Task)
        .where(Task.id == task_id, Task.owner_id == current_user_id)
        .with_for_update()
    )
    task = (await db.execute(stmt)).scalars().first()

    if not task:
        raise HTTPException(status_code=404, detail="Task မတွေ့ရှိပါ (သို့) မိမိ၏ Task မဟုတ်ပါ။")
    if task.status not in {TaskStatus.DRAFT, TaskStatus.REJECTED}:
        raise HTTPException(
            status_code=409,
            detail="Only draft or rejected tasks can be submitted for review.",
        )
    
    try:
        task.status = TaskStatus.PENDING_L1
        record_activity(
            db,
            actor_id=current_user_id,
            action="TASK_SUBMITTED",
            entity_type="task",
            entity_id=task.id,
            description=f"Submitted task '{getattr(task, 'title', task.id)}' for review",
        )
        await db.commit()
        await db.refresh(task)
        return task
    except Exception as e:
        await db.rollback()
        raise e

# ==============================================================================
# ၂။ L1 Manager မှ စစ်ဆေးခြင်း
# ==============================================================================
async def review_task_l1(db: AsyncSession, task_id: UUID, payload: TaskReviewInput, current_user_id: UUID) -> Task:
    stmt = select(Task).options(
        selectinload(Task.owner).selectinload(User.user_departments)
    ).where(Task.id == task_id).with_for_update()
    
    task = (await db.execute(stmt)).scalars().first()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task မတွေ့ရှိပါ။")

    if task.status != TaskStatus.PENDING_L1:
        raise HTTPException(status_code=400, detail="ဤ Task သည် L1 စစ်ဆေးရန် အခြေအနေတွင် မရှိပါ။")

    if payload.action in ["DELAY", "REJECTED"] and not payload.comments:
        raise HTTPException(status_code=400, detail="မှတ်ချက် (Comments) မဖြစ်မနေ ထည့်သွင်းပေးရပါမည်။")

    if payload.action == "REJECTED" and not payload.deduction_rule:
        raise HTTPException(status_code=400, detail="KPI ဖြတ်မည့် Deduction Rule အား ရွေးချယ်ပေးရပါမည်။")

    await _authorize_reviewer(db, task, current_user_id, "L1")

    try:
        task.status = {
            "APPROVED": TaskStatus.PENDING_L0,
            "REJECTED": TaskStatus.REJECTED,
            "DELAY": TaskStatus.REJECTED,
        }[payload.action]
        
        final_remarks = payload.comments
        if payload.action == "REJECTED" and payload.deduction_rule:
            final_remarks = f"[KPI Deduction: {payload.deduction_rule}] {payload.comments}"
            
        review = TaskReview(
            task_id=task.id, 
            reviewer_id=current_user_id, 
            reviewer_level="L1", 
            action=payload.action,
            comments=final_remarks
        )
        db.add(review)
        status_msg = {
            "APPROVED": "Approved",
            "REJECTED": "Rejected",
        }.get(payload.action, "Delayed")
        record_activity(
            db,
            actor_id=current_user_id,
            action=f"TASK_L1_{payload.action}",
            entity_type="task",
            entity_id=task.id,
            description=f"{status_msg} task '{task.title}' at L1 review",
        )
        await create_notification(
            db,
            {
                "user_id": task.owner_id,
                "title": f"Task {status_msg} (L1)",
                "message": f"Your task '{task.title}' has been {status_msg.lower()}.",
                "notification_type": payload.action,
                "channel": "DASHBOARD",
            },
            commit=False,
        )
        await db.flush()
        if payload.action in ["APPROVED", "REJECTED"]:
            target_date = task.due_date if task.due_date else datetime.now(timezone)
            
            owner_dept_id = None
            if task.owner and task.owner.user_departments:
                owner_dept_id = task.owner.user_departments[0].department_id
                
            await trigger_kpi_recalculation(
                db=db, user_level="L2", user_id=task.owner_id, 
                department_id=owner_dept_id, target_date=target_date, commit=False
            )
        await db.commit()
        return task

    except Exception as e:
        await db.rollback()
        raise e

# ==============================================================================
# ၃။ L0 Director မှ နောက်ဆုံးအတည်ပြုခြင်း (L1 ကို Penalty ပေးနိုင်သည်)
# ==============================================================================
async def _l0_kpi_target(db: AsyncSession, task: Task, payload: TaskReviewInput, owner_dept_id):
    if payload.action == "APPROVED":
        task.status = TaskStatus.COMPLETED
        return task.owner_id, "L2", owner_dept_id
    if payload.action == "DELAY":
        task.status = TaskStatus.REJECTED
        return None, None, None
    if payload.action != "REJECTED":
        return None, None, None

    task.status = TaskStatus.PENDING_L1
    l1_review_stmt = select(TaskReview).where(
        TaskReview.task_id == task.id,
        TaskReview.reviewer_level == "L1",
        TaskReview.action == "APPROVED",
    ).order_by(TaskReview.created_at.desc())
    l1_review = (await db.execute(l1_review_stmt)).scalars().first()
    if not l1_review:
        return None, None, None
    l1_ud_stmt = select(UserDepartment).where(
        UserDepartment.user_id == l1_review.reviewer_id
    )
    l1_ud = (await db.execute(l1_ud_stmt)).scalars().first()
    return (
        l1_review.reviewer_id,
        "L1",
        l1_ud.department_id if l1_ud else owner_dept_id,
    )


def _l0_review_remarks(payload: TaskReviewInput):
    if payload.action == "REJECTED" and payload.deduction_rule:
        return f"[KPI Deduction: {payload.deduction_rule}] {payload.comments}"
    return payload.comments


def _l0_status_message(action: str) -> str:
    if action == "APPROVED":
        return "Approved"
    if action == "REJECTED":
        return "Rejected"
    return "Delayed"


async def _notify_l0_review(
    db: AsyncSession,
    task: Task,
    payload: TaskReviewInput,
    status_msg: str,
    target_kpi_user_id,
    target_kpi_level,
):
    await create_notification(
        db,
        {
            "user_id": task.owner_id,
            "title": f"Task {status_msg} (L0)",
            "message": (
                f"Your task '{task.title}' has been {status_msg.lower()} by Top Management."
            ),
            "notification_type": payload.action,
            "channel": "DASHBOARD",
        },
        commit=False,
    )
    if payload.action == "REJECTED" and target_kpi_level == "L1" and target_kpi_user_id:
        await create_notification(
            db,
            {
                "user_id": target_kpi_user_id,
                "title": "L1 Penalty Applied",
                "message": (
                    f"A task you previously approved ('{task.title}') was rejected by L0. "
                    "A penalty has been applied to your KPI."
                ),
                "notification_type": "ESCALATION",
                "channel": "DASHBOARD",
            },
            commit=False,
        )


async def verify_task_l0(db: AsyncSession, task_id: UUID, payload: TaskReviewInput, current_user_id: UUID) -> Task:
    stmt = select(Task).options(
        selectinload(Task.owner).selectinload(User.user_departments)
    ).where(Task.id == task_id).with_for_update()
    
    task = (await db.execute(stmt)).scalars().first()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task မတွေ့ရှိပါ။")

    if task.status != TaskStatus.PENDING_L0:
        raise HTTPException(status_code=400, detail="ဤ Task သည် L0 စစ်ဆေးရန် အခြေအနေတွင် မရှိပါ။")

    if payload.action in ["DELAY", "REJECTED"] and not payload.comments:
        raise HTTPException(status_code=400, detail="မှတ်ချက် (Comments) မဖြစ်မနေ ထည့်သွင်းပေးရပါမည်။")

    if payload.action == "REJECTED" and not payload.deduction_rule:
        raise HTTPException(status_code=400, detail="KPI ဖြတ်မည့် Deduction Rule အား ရွေးချယ်ပေးရပါမည်။")

    await _authorize_reviewer(db, task, current_user_id, "L0")

    try:
        owner_dept_id = None
        if task.owner and task.owner.user_departments:
            owner_dept_id = task.owner.user_departments[0].department_id

        target_kpi_user_id, target_kpi_level, target_kpi_dept = await _l0_kpi_target(
            db, task, payload, owner_dept_id
        )

        final_remarks = _l0_review_remarks(payload)
        review = TaskReview(
            task_id=task.id, 
            reviewer_id=current_user_id, 
            reviewer_level="L0", 
            action="VERIFIED" if payload.action == "APPROVED" else "REJECTED_OVERRIDE",
            comments=final_remarks,
            is_verified_by_l0=True
        )
        db.add(review)
        status_msg = _l0_status_message(payload.action)
        record_activity(
            db,
            actor_id=current_user_id,
            action=f"TASK_L0_{payload.action}",
            entity_type="task",
            entity_id=task.id,
            description=f"{status_msg} task '{task.title}' at L0 review",
        )
        await _notify_l0_review(
            db,
            task,
            payload,
            status_msg,
            target_kpi_user_id,
            target_kpi_level,
        )
        await db.flush()
        if target_kpi_user_id and target_kpi_dept:
            target_date = task.due_date if task.due_date else datetime.now(timezone)
            await trigger_kpi_recalculation(
                db=db, user_level=target_kpi_level, user_id=target_kpi_user_id, 
                department_id=target_kpi_dept, target_date=target_date, commit=False
            )
        await db.commit()
            
        return task

    except Exception as e:
        await db.rollback()
        raise e
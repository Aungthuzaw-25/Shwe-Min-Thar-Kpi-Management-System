from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload, selectinload
from fastapi import HTTPException, status
from uuid import UUID
from datetime import datetime
import pytz

from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.tasks.models.task_review import TaskReview 
from app.modules.users.models import User
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.engine import trigger_kpi_recalculation
from app.modules.tasks.schemas import TaskCreate, TaskUpdate, TaskReviewInput

# 🌟 FIX: Updated import to use the refactored module-level function
from app.modules.notifications.repo import create_notification
from app.core.timezone import APPLICATION_TIMEZONE

timezone = APPLICATION_TIMEZONE

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
        await db.commit()
        await db.refresh(new_task)
        return new_task
    except Exception as e:
        await db.rollback()
        raise e

async def submit_task(db: AsyncSession, task_id: UUID, current_user_id: UUID) -> Task:
    stmt = select(Task).where(Task.id == task_id, Task.owner_id == current_user_id)
    task = (await db.execute(stmt)).scalars().first()

    if not task:
        raise HTTPException(status_code=404, detail="Task မတွေ့ရှိပါ (သို့) မိမိ၏ Task မဟုတ်ပါ။")
    
    try:
        task.status = TaskStatus.PENDING_L1
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
        joinedload(Task.owner).selectinload(User.user_departments)
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

    try:
        if payload.action == "APPROVED":
            task.status = TaskStatus.PENDING_L0
        else:
            task.status = TaskStatus.REJECTED 
        
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
        await db.commit()

        # 🌟 TRIGGER: L1 Review Notification (Fixed import)
        try:
            status_msg = "Approved" if payload.action == "APPROVED" else "Rejected" if payload.action == "REJECTED" else "Delayed"
            await create_notification(db, {
                "user_id": task.owner_id,
                "title": f"Task {status_msg} (L1)",
                "message": f"Your task '{task.title}' has been {status_msg.lower()}.",
                "notification_type": payload.action,
                "channel": "DASHBOARD"
            })
        except Exception:
            pass
        
        if payload.action in ["APPROVED", "REJECTED"]:
            target_date = task.due_date if task.due_date else datetime.now(timezone)
            
            owner_dept_id = None
            if task.owner and task.owner.user_departments:
                owner_dept_id = task.owner.user_departments[0].department_id
                
            await trigger_kpi_recalculation(
                db=db, user_level="L2", user_id=task.owner_id, 
                department_id=owner_dept_id, target_date=target_date
            )
        return task

    except Exception as e:
        await db.rollback()
        raise e

# ==============================================================================
# ၃။ L0 Director မှ နောက်ဆုံးအတည်ပြုခြင်း (L1 ကို Penalty ပေးနိုင်သည်)
# ==============================================================================
async def verify_task_l0(db: AsyncSession, task_id: UUID, payload: TaskReviewInput, current_user_id: UUID) -> Task:
    stmt = select(Task).options(
        joinedload(Task.owner).selectinload(User.user_departments)
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

    try:
        owner_dept_id = None
        if task.owner and task.owner.user_departments:
            owner_dept_id = task.owner.user_departments[0].department_id

        target_kpi_user_id = None
        target_kpi_level = None
        target_kpi_dept = None

        if payload.action == "APPROVED":
            task.status = TaskStatus.COMPLETED
            target_kpi_user_id = task.owner_id
            target_kpi_level = "L2"
            target_kpi_dept = owner_dept_id
        elif payload.action == "DELAY":
            task.status = TaskStatus.REJECTED 
        elif payload.action == "REJECTED":
            task.status = TaskStatus.PENDING_L1 
            
            l1_review_stmt = select(TaskReview).where(
                TaskReview.task_id == task.id, TaskReview.reviewer_level == 'L1', TaskReview.action == 'APPROVED'
            ).order_by(TaskReview.created_at.desc())
            l1_review = (await db.execute(l1_review_stmt)).scalars().first()
            
            if l1_review:
                target_kpi_user_id = l1_review.reviewer_id
                target_kpi_level = "L1"
                
                from app.modules.departments.models import UserDepartment
                l1_ud_stmt = select(UserDepartment).where(UserDepartment.user_id == l1_review.reviewer_id)
                l1_ud = (await db.execute(l1_ud_stmt)).scalars().first()
                target_kpi_dept = l1_ud.department_id if l1_ud else owner_dept_id

        final_remarks = payload.comments
        if payload.action == "REJECTED" and payload.deduction_rule:
             final_remarks = f"[KPI Deduction: {payload.deduction_rule}] {payload.comments}"

        review = TaskReview(
            task_id=task.id, 
            reviewer_id=current_user_id, 
            reviewer_level="L0", 
            action="VERIFIED" if payload.action == "APPROVED" else "REJECTED_OVERRIDE",
            comments=final_remarks,
            is_verified_by_l0=True
        )
        db.add(review)
        await db.commit()

        # 🌟 TRIGGER: L0 Review & Penalty Notification (Fixed import)
        try:
            status_msg = "Approved" if payload.action == "APPROVED" else "Rejected" if payload.action == "REJECTED" else "Delayed"
            
            # 1. ဝန်ထမ်းဆီသို့ အကြောင်းကြားခြင်း
            await create_notification(db, {
                "user_id": task.owner_id,
                "title": f"Task {status_msg} (L0)",
                "message": f"Your task '{task.title}' has been {status_msg.lower()} by Top Management.",
                "notification_type": payload.action,
                "channel": "DASHBOARD"
            })

            # 2. L1 Supervisor အား အမှတ်ဖြတ်ခံရလျှင် သတိပေးခြင်း
            if payload.action == "REJECTED" and target_kpi_level == "L1" and target_kpi_user_id:
                await create_notification(db, {
                    "user_id": target_kpi_user_id,
                    "title": "L1 Penalty Applied",
                    "message": f"A task you previously approved ('{task.title}') was rejected by L0. A penalty has been applied to your KPI.",
                    "notification_type": "ESCALATION",
                    "channel": "DASHBOARD"
                })
        except Exception:
            pass
        
        if target_kpi_user_id and target_kpi_dept:
            target_date = task.due_date if task.due_date else datetime.now(timezone)
            await trigger_kpi_recalculation(
                db=db, user_level=target_kpi_level, user_id=target_kpi_user_id, 
                department_id=target_kpi_dept, target_date=target_date
            )
            
        return task

    except Exception as e:
        await db.rollback()
        raise e
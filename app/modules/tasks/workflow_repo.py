import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload
from typing import List, Optional

from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.tasks.models.submission_batch import SubmissionBatch
from app.modules.tasks.models.approval_queue import ApprovalQueue
from app.modules.tasks.models.task_history import TaskHistory
from app.modules.tasks.models.task_review import TaskReview

# 🌟 FIX: Updated Import to match the refactored notification repo
from app.modules.notifications.repo import create_notification

async def submit_task_batch(
    db: AsyncSession, 
    task_ids: List[uuid.UUID], 
    submitter_id: uuid.UUID, 
    reviewer_id: uuid.UUID
) -> SubmissionBatch:
    stmt = select(Task).where(
        Task.id.in_(task_ids), 
        Task.owner_id == submitter_id,
        Task.status.in_([TaskStatus.DRAFT, TaskStatus.REJECTED])
    )
    result = await db.execute(stmt)
    tasks = result.scalars().all()
    requested_ids = set(task_ids)
    if not tasks or {task.id for task in tasks} != requested_ids:
        raise ValueError("တင်ပြရန် သင့်လျော်သော Task များ မတွေ့ရှိပါ။")
        
    if not reviewer_id:
        raise ValueError("Review လုပ်ပေးမည့် Manager အား ရွေးချယ်ပေးပါ။")

    try:
        batch = SubmissionBatch(submitter_id=submitter_id, total_tasks=len(tasks), status="PENDING")
        db.add(batch)
        await db.flush() 
        
        queue = ApprovalQueue(batch_id=batch.id, reviewer_id=reviewer_id, pending_count=len(tasks))
        db.add(queue)
        
        for task in tasks:
            task.batch_id = batch.id
            task.assigner_id = reviewer_id  
            old_status = getattr(task.status, 'value', str(task.status))
            task.status = TaskStatus.PENDING_L1
            db.add(TaskHistory(
                task_id=task.id, 
                changed_by=submitter_id, 
                action="BATCH_SUBMITTED", 
                old_value=old_status, 
                new_value=TaskStatus.PENDING_L1.value
            ))
            
        await db.commit()

        # 🌟 TRIGGER: Notification ပို့ခြင်း
        try:
            await create_notification(db, {
                "user_id": reviewer_id,
                "title": "New Task Submission",
                "message": f"A new batch of {len(tasks)} tasks has been submitted for your review.",
                "notification_type": "APPROVAL",
                "channel": "DASHBOARD"
            })
        except Exception:
            pass # Notification error ကြောင့် ပင်မ Business Logic မရပ်သွားစေရန်

        return batch

    except Exception as e:
        await db.rollback()
        raise e

async def get_my_approval_queue(db: AsyncSession, reviewer_id: uuid.UUID):
    stmt = select(ApprovalQueue).options(
        joinedload(ApprovalQueue.batch).joinedload(SubmissionBatch.submitter)
    ).where(
        ApprovalQueue.reviewer_id == reviewer_id,
        ApprovalQueue.pending_count > 0
    ).order_by(ApprovalQueue.created_at.desc())
    
    result = await db.execute(stmt)
    # ⚡ FIX: unique() added to prevent collection duplication in joins
    return result.unique().scalars().all()

async def review_task_batch(
    db: AsyncSession, 
    batch_id: uuid.UUID, 
    reviewer_id: uuid.UUID, 
    reviewer_level: str, 
    action: str, 
    comments: str, 
    next_reviewer_id: Optional[uuid.UUID] = None, 
    deduction_rule: Optional[str] = None
) -> SubmissionBatch:
    stmt = select(SubmissionBatch).options(joinedload(SubmissionBatch.tasks)).where(SubmissionBatch.id == batch_id)
    result = await db.execute(stmt)
    # ⚡ FIX: unique() is strictly required in SQLAlchemy 2.0 when joinedload against collections
    batch = result.unique().scalar_one_or_none()
    
    if not batch or not batch.tasks:
        raise ValueError("Batch မတွေ့ရှိပါ သို့မဟုတ် Task များ မရှိပါ။")

    q_stmt = select(ApprovalQueue).where(ApprovalQueue.batch_id == batch_id, ApprovalQueue.reviewer_id == reviewer_id)
    q_result = await db.execute(q_stmt)
    current_queue = q_result.scalar_one_or_none()

    try:
        passed_count = 0 

        for task in batch.tasks:
            new_status = task.status
            
            # 🌟 Workflow Rules Logic
            if action == "APPROVED" and reviewer_level == 'L1' and task.status == TaskStatus.PENDING_L1:
                new_status = TaskStatus.PENDING_L0
                passed_count += 1
            elif action == "APPROVED" and reviewer_level == 'L0' and task.status == TaskStatus.PENDING_L0:
                new_status = TaskStatus.APPROVED
                passed_count += 1
            elif task.status == TaskStatus.REJECTED:
                continue
            elif action == "DELAY":
                new_status = TaskStatus.REJECTED 
            elif action == "REJECTED":
                if reviewer_level == 'L1':
                    new_status = TaskStatus.REJECTED 
                elif reviewer_level == 'L0':
                    new_status = TaskStatus.PENDING_L1 
                
            if task.status != new_status:
                old_status_val = getattr(task.status, 'value', str(task.status))
                new_status_val = getattr(new_status, 'value', str(new_status))
                
                final_remarks = comments
                if action == "REJECTED" and deduction_rule:
                    final_remarks = f"[KPI Deduction: {deduction_rule}] {comments}"

                db.add(TaskHistory(
                    task_id=task.id, 
                    changed_by=reviewer_id, 
                    action=f"BATCH_{action}", 
                    old_value=old_status_val, 
                    new_value=new_status_val, 
                    remarks=final_remarks
                ))
                task.status = new_status
                db.add(TaskReview(
                    task_id=task.id, 
                    reviewer_id=reviewer_id, 
                    reviewer_level=reviewer_level, 
                    action=action, 
                    comments=final_remarks
                ))

        if current_queue:
            current_queue.pending_count = 0
            current_queue.completed_count = len(batch.tasks)

        if action == "APPROVED" and reviewer_level == 'L1' and next_reviewer_id:
            if passed_count > 0:
                new_queue = ApprovalQueue(batch_id=batch.id, reviewer_id=next_reviewer_id, pending_count=passed_count)
                db.add(new_queue)
            batch.status = "PARTIALLY_APPROVED"
        elif action == "APPROVED" and reviewer_level == 'L0':
            batch.status = "COMPLETED"
        else:
            batch.status = action

        await db.commit()

        # 🌟 TRIGGER: Task ပိုင်ရှင်ဆီသို့ Notification ပို့ခြင်း
        try:
            status_msg = "Approved" if action == "APPROVED" else "Rejected" if action == "REJECTED" else "Delayed"
            await create_notification(db, {
                "user_id": batch.submitter_id,
                "title": f"Batch Review: {status_msg}",
                "message": f"Your submitted tasks have been {status_msg.lower()} by Level {reviewer_level[-1]}.",
                "notification_type": action,
                "channel": "DASHBOARD"
            })

            # L1 မှ Approve လုပ်၍ L0 ဆီသို့ ဆက်ပို့သောအခါ L0 ဆီသို့ Notification ပို့ခြင်း
            if action == "APPROVED" and reviewer_level == 'L1' and next_reviewer_id:
                await create_notification(db, {
                    "user_id": next_reviewer_id,
                    "title": "Tasks Forwarded for Final Approval",
                    "message": "A batch of tasks has been approved by L1 and forwarded to you for final review.",
                    "notification_type": "APPROVAL",
                    "channel": "DASHBOARD"
                })
        except Exception:
            pass

        return batch

    except Exception as e:
        await db.rollback()
        raise e

async def get_task_timeline(db: AsyncSession, task_id: uuid.UUID):
    stmt = select(TaskHistory).options(joinedload(TaskHistory.user)).where(
        TaskHistory.task_id == task_id
    ).order_by(TaskHistory.created_at.desc())
    
    result = await db.execute(stmt)
    return result.unique().scalars().all()
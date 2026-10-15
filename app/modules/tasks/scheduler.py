from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.background_jobs.reminders import check_and_send_task_reminders
from app.core.timezone import APPLICATION_TIMEZONE
from app.modules.tasks.models.task import Task, TaskFrequency, TaskStatus
from app.modules.tasks.models.task_history import TaskHistory

timezone = APPLICATION_TIMEZONE

async def generate_recurring_tasks(db: AsyncSession):
    """Daily, Weekly, Monthly Task များကို အလိုအလျောက် ဖန်တီးပေးမည့် Service"""
    now = datetime.now(timezone)
    
    # 🌟 Task များနှင့် ၎င်းတို့၏ Owner (User) အချက်အလက်များကိုပါ တစ်ပါတည်း ဆွဲထုတ်မည်
    stmt = select(Task).options(selectinload(Task.owner)).where(
        Task.frequency != TaskFrequency.ONCE, 
        Task.next_run_date <= now,
        Task.is_deleted.is_(False),
    )
    result = await db.execute(stmt)
    tasks = result.scalars().all()
    
    for task in tasks:
        owner = task.owner
        
        # 🌟 User သည် ခွင့်ရက်အတွင်း ရောက်နေပါက Task အသစ် မမွေးဖွားဘဲ ကျော်သွားမည်
        if owner.is_on_leave and owner.leave_start_date and owner.leave_end_date:
            if owner.leave_start_date <= now <= owner.leave_end_date:
                print(f"Skipping auto-task for {owner.email} (On Leave)")
                
                # Task အသစ်မဖန်တီးသော်လည်း နောက်တစ်ကြိမ် Date ကိုမူ Update လုပ်ပေးရမည် (Stuck မဖြစ်စေရန်)
                _advance_next_run_date(task, now)
                db.add(task)
                continue

        # ခွင့်မယူထားပါက ပုံမှန်အတိုင်း Task အသစ် ဖန်တီးမည်
        new_task = Task(
            title=task.title,
            description=task.description,
            owner_id=task.owner_id,
            assigner_id=task.assigner_id,
            status=TaskStatus.DRAFT,
            frequency=task.frequency
        )
        db.add(new_task)
        
        # Next run date ကို Update ပြန်လုပ်မည်
        _advance_next_run_date(task, now)
            
        db.add(task)
            
    await db.commit()
    print(f"✅ Generated {len(tasks)} recurring tasks.")

async def process_task_escalation(db: AsyncSession):
    """L1 သို့မဟုတ် L0 တွင် (၃) ရက်ထက်ကျော်လွန်ပြီး Pending ဖြစ်နေပါက အထက်သို့ အလိုအလျောက် တင်ပြမည်"""
    escalation_threshold = datetime.now(timezone) - timedelta(days=3)
    
    stmt = select(Task).where(
        Task.status.in_([TaskStatus.PENDING_L1, TaskStatus.PENDING_L0]),
        Task.updated_at <= escalation_threshold,
        Task.is_escalated == False,
        Task.is_deleted.is_(False),
    )
    result = await db.execute(stmt)
    stale_tasks = result.scalars().all()
    
    for task in stale_tasks:
        task.is_escalated = True
        task.escalated_at = datetime.now(timezone)
        
        old_status = task.status.value
        # L1 မှာ ကြန့်ကြာနေရင် L0 ဆီ တိုက်ရိုက်ပို့မည်
        if task.status == TaskStatus.PENDING_L1:
            task.status = TaskStatus.PENDING_L0
            task.escalated_to_level = "L0"
            
        # မှတ်တမ်းတင်မည်
        db.add(TaskHistory(
            task_id=task.id, action="ESCALATED", old_value=old_status, 
            new_value=task.status.value, remarks="အချိန်ကျော်လွန်သွားသဖြင့် အလိုအလျောက် Escalate လုပ်ပါသည်"
        ))
        
    await db.commit()
    print(f"⚠️ Escalated {len(stale_tasks)} stale tasks.")

async def send_task_reminders(db: AsyncSession):
    """Dispatch the existing dashboard deadline reminders under the scheduler lock."""
    await check_and_send_task_reminders(db)


def _advance_next_run_date(task: Task, now: datetime) -> None:
    if task.frequency == TaskFrequency.DAILY:
        task.next_run_date = now + timedelta(days=1)
    elif task.frequency == TaskFrequency.WEEKLY:
        task.next_run_date = now + timedelta(weeks=1)
    elif task.frequency == TaskFrequency.MONTHLY:
        task.next_run_date = now + timedelta(days=30)
    elif task.frequency == TaskFrequency.YEARLY:
        scheduled_date = task.next_run_date or now
        try:
            task.next_run_date = scheduled_date.replace(year=scheduled_date.year + 1)
        except ValueError:
            task.next_run_date = scheduled_date.replace(
                year=scheduled_date.year + 1,
                day=28,
            )
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.modules.tasks.models.task import Task
from app.modules.tasks.models.task_evidence import TaskEvidence
from app.modules.tasks.models.task_review import TaskReview


# 🌟 ARCHITECTURE FIX: Reusable Base Query (DRY Principle ကို လိုက်နာထားသည်)
def get_task_base_query():
    return select(Task).options(
        selectinload(Task.reviews),
        selectinload(Task.evidence)
        # 💡 Tip: Frontend မှာ Task တင်တဲ့သူရဲ့ နာမည်ပြဖို့လိုရင် 
        # selectinload(Task.owner) သို့မဟုတ် selectinload(Task.user) ကို ဒီနေရာမှာ တစ်ခါတည်း ထည့်ပါ။
    )

async def create_task(db: AsyncSession, task_data: dict) -> Task:
    new_task = Task(**task_data)
    db.add(new_task)
    await db.commit()
    
    # Base Query ကို ပြန်သုံးထားသောကြောင့် Code ပိုရှင်းသွားသည်
    stmt = get_task_base_query().where(Task.id == new_task.id)
    result = await db.execute(stmt)
    return result.scalar_one()

async def get_task_by_id(db: AsyncSession, task_id: uuid.UUID) -> Task | None:
    stmt = get_task_base_query().where(Task.id == task_id, Task.is_deleted == False)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

# ⚡ PERFORMANCE FIX: Added skip and limit for Pagination
async def get_all_tasks(db: AsyncSession, skip: int = 0, limit: int = 100):
    """
    Task အားလုံးကို ဆွဲထုတ်မည့်အစား Memory မပြည့်စေရန် limit ဖြင့် ခွဲထုတ်မည်။
    """
    stmt = get_task_base_query().where(Task.is_deleted == False).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()

async def update_task(db: AsyncSession, task: Task, update_data: dict) -> Task:
    for key, value in update_data.items():
        setattr(task, key, value)
    await db.commit()
    
    stmt = get_task_base_query().where(Task.id == task.id)
    result = await db.execute(stmt)
    return result.scalar_one()

async def add_task_review(db: AsyncSession, review_data: dict) -> TaskReview:
    new_review = TaskReview(**review_data)
    db.add(new_review)
    await db.commit()
    await db.refresh(new_review)
    return new_review

# 🌟 Phase 4: Evidence မှတ်တမ်းတင်ရန်
async def add_task_evidence(db: AsyncSession, evidence_data: dict) -> TaskEvidence:
    new_evidence = TaskEvidence(**evidence_data)
    db.add(new_evidence)
    await db.commit()
    await db.refresh(new_evidence)
    return new_evidence

# 🛡️ SECURITY FIX: Missing Soft Delete Function
async def soft_delete_task(db: AsyncSession, task: Task) -> Task:
    """Task များကို အပြီးတိုင်မဖျက်ဘဲ မှတ်တမ်းကျန်စေရန် Soft Delete လုပ်မည်"""
    task.is_deleted = True
    await db.commit()
    return task
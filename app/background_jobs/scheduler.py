from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import text
from datetime import datetime, timedelta
import pytz
import logging
import hashlib

from app.database.connection import AsyncSessionLocal
from app.modules.kpi.models import KPIRecord, PeriodType 
# 🌟 KPI Engine အသစ်ကို Import ပြုလုပ်ခြင်း
from app.modules.kpi.engine import KPICalculationEngine
from app.modules.users.models import User

from app.modules.tasks.scheduler import generate_recurring_tasks, process_task_escalation, send_task_reminders
from app.background_jobs.notification_jobs import process_batch_notifications
from app.core.timezone import APPLICATION_TIMEZONE

logger = logging.getLogger(__name__)
timezone = APPLICATION_TIMEZONE


async def _run_with_lock(job_name, job):
    """Run a scheduled job only while holding its PostgreSQL advisory lock."""
    async with AsyncSessionLocal() as db:
        lock_key = int.from_bytes(
            hashlib.sha256(job_name.encode("utf-8")).digest()[:8],
            "big",
        ) % 2147483647
        acquired = await db.scalar(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
        if not acquired:
            logger.warning("Skipping scheduler job already running: %s", job_name)
            return
        try:
            await job()
        finally:
            await db.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": lock_key},
            )
            await db.commit()


async def _locked_weekly_kpi():
    await _run_with_lock("kpi.weekly_rollup", generate_weekly_kpi)


async def _locked_monthly_kpi():
    await _run_with_lock("kpi.monthly_rollup", generate_monthly_kpi)


async def _locked_recurring_tasks():
    await _run_with_lock("tasks.recurring", run_recurring_tasks_job)


async def _locked_escalation():
    await _run_with_lock("tasks.escalation", run_escalation_job)


async def _locked_notifications():
    await _run_with_lock(
        "notifications.batch",
        lambda: _run_sync_notification_job(),
    )


async def _run_sync_notification_job():
    process_batch_notifications()

# ==============================================================================
# 1. KPI Rollup Tasks (Weekly & Monthly)
# ==============================================================================
async def generate_weekly_kpi():
    """လွန်ခဲ့သော တစ်ပတ်စာ Daily KPI များကို ပေါင်းချုပ်၍ Weekly KPI အဖြစ်ပြောင်းခြင်း"""
    logger.info("Starting Weekly KPI Rollup Task...")
    
    today = datetime.now(timezone).date()
    start_of_this_week = today - timedelta(days=today.weekday())
    start_of_last_week = start_of_this_week - timedelta(days=7)
    end_of_last_week = start_of_this_week - timedelta(days=1)

    # Convert to datetime with timezone for engine
    period_start = datetime.combine(start_of_last_week, datetime.min.time())
    period_end = datetime.combine(end_of_last_week, datetime.max.time())

    async with AsyncSessionLocal() as db:
        try:
            # User အားလုံးကို ဆွဲထုတ်၍ Weekly KPI တွက်ချက်မည်
            stmt = select(User).where(User.is_active == True, User.department_id != None)
            users = (await db.execute(stmt)).scalars().all()
            
            for user in users:
                await KPICalculationEngine.execute_kpi_calculation(
                    db=db, 
                    user=user, 
                    period_start=period_start, 
                    period_end=period_end, 
                    period_type=PeriodType.WEEKLY
                )
                
            await db.commit()
            logger.info("✅ Weekly KPI Rollup completed successfully.")
        except Exception:
            await db.rollback()
            logger.exception("Error in Weekly KPI Rollup")

async def generate_monthly_kpi():
    """လွန်ခဲ့သော တစ်လစာ KPI များကို ပေါင်းချုပ်၍ Monthly Final KPI အဖြစ် အတည်ပြုခြင်း"""
    logger.info("Starting Monthly KPI Final Rollup Task...")
    
    today = datetime.now(timezone).date()
    first_day_of_this_month = today.replace(day=1)
    end_of_last_month = first_day_of_this_month - timedelta(days=1)
    start_of_last_month = end_of_last_month.replace(day=1)
    
    # Convert to datetime with timezone for engine
    period_start = datetime.combine(start_of_last_month, datetime.min.time())
    period_end = datetime.combine(end_of_last_month, datetime.max.time())

    async with AsyncSessionLocal() as db:
        try:
            # User အားလုံးကို ဆွဲထုတ်၍ Monthly KPI တွက်ချက်မည်
            stmt = select(User).where(User.is_active == True, User.department_id != None)
            users = (await db.execute(stmt)).scalars().all()
            
            for user in users:
                await KPICalculationEngine.execute_kpi_calculation(
                    db=db, 
                    user=user, 
                    period_start=period_start, 
                    period_end=period_end, 
                    period_type=PeriodType.MONTHLY
                )

            await db.commit()
            logger.info("✅ Monthly KPI Final Rollup completed successfully.")
        except Exception:
            await db.rollback()
            logger.exception("Error in Monthly KPI Rollup")

# ==============================================================================
# 2. Workflow Tasks Wrappers (Task Scheduler Async Bridge)
# ==============================================================================
async def run_recurring_tasks_job():
    """DB Session ဖွင့်၍ Recurring Tasks များကို အလိုအလျောက် ဖန်တီးပေးမည့် Wrapper"""
    logger.info("Starting Recurring Tasks Job...")
    async with AsyncSessionLocal() as db:
        try:
            await generate_recurring_tasks(db)
        except Exception:
            logger.exception("Error in Recurring Tasks Job")

async def run_escalation_job():
    """DB Session ဖွင့်၍ Pending ကြာနေသော Task များကို Escalation လုပ်မည့် Wrapper"""
    logger.info("Starting Task Escalation Job...")
    async with AsyncSessionLocal() as db:
        try:
            await process_task_escalation(db)
            await send_task_reminders(db)
        except Exception:
            logger.exception("Error in Task Escalation Job")

# ==============================================================================
# 3. Main Scheduler Controller
# ==============================================================================
def start_scheduler():
    """FastAPI မောင်းနှင်သည်နှင့် Cron Jobs အားလုံးကို Background တွင် အလုပ်လုပ်စေရန်"""
    scheduler = AsyncIOScheduler(timezone=timezone)
    
    # --------------------------------------------------------------------------
    # A. KPI Rollup Schedule (Weekly & Monthly)
    # --------------------------------------------------------------------------
    scheduler.add_job(_locked_weekly_kpi, 'cron', day_of_week='mon', hour=1, minute=0,
                      id='weekly_kpi_rollup', coalesce=True, max_instances=1)
    scheduler.add_job(_locked_monthly_kpi, 'cron', day=1, hour=2, minute=0,
                      id='monthly_kpi_rollup', coalesce=True, max_instances=1)

    # --------------------------------------------------------------------------
    # B. Workflow & Task Management Schedule (မူလအတိုင်း အပြည့်အစုံ ပါရှိသည်)
    # --------------------------------------------------------------------------
    # နေ့စဉ် ည ၁၂ နာရီခွဲ (00:30 AM) - နောက်တစ်နေ့အတွက် Recurring Tasks အသစ်မွေးဖွားရန်
    scheduler.add_job(_locked_recurring_tasks, 'cron', hour=0, minute=30,
                      id='recurring_tasks', coalesce=True, max_instances=1)
    
    # နေ့စဉ် ညနေ ၆ နာရီ (06:00 PM) - ရုံးဆင်းချိန်တွင် ကြန့်ကြာနေသော Task များကို Escalation စစ်ရန်
    scheduler.add_job(_locked_escalation, 'cron', hour=18, minute=0,
                      id='task_escalation', coalesce=True, max_instances=1)

    # --------------------------------------------------------------------------
    # C. Notifications Schedule
    # --------------------------------------------------------------------------
    # Grouped/Batch Notifications များကို မိနစ် ၃၀ တစ်ခါ ပို့ပေးရန်
    scheduler.add_job(
        _locked_notifications,
        'interval', 
        minutes=30, 
        id='batch_notification_job',
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.start()
    logger.info("🚀 System Scheduler (KPI & Workflow Engine & Notifications) started successfully.")
    return scheduler
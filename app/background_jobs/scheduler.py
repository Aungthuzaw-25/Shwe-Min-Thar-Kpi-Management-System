import asyncio
import hashlib
import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy.future import select

from app.background_jobs.notification_jobs import process_batch_notifications
from app.core.timezone import APPLICATION_TIMEZONE
from app.database.connection import AsyncSessionLocal

# 🌟 KPI Engine အသစ်ကို Import ပြုလုပ်ခြင်း
from app.modules.kpi.engine import KPICalculationEngine
from app.modules.kpi.models import PeriodType
from app.modules.kpi.period_ranges import calendar_period_range
from app.modules.tasks.scheduler import (
    generate_recurring_tasks,
    process_task_escalation,
    send_task_reminders,
)
from app.modules.departments.models import UserDepartment
from app.modules.system.audit_log_retention import run_audit_log_retention_cleanup
from app.modules.users.models import User

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
        _run_sync_notification_job,
    )


async def _locked_audit_log_retention():
    await _run_with_lock("audit_logs.retention", run_audit_log_retention_job)


async def run_yearly_report_reminder_job(now=None) -> int:
    """December reminder: until the company-wide full-year report for
    the current year has been downloaded, notify every Super Admin —
    on Dec 1, then weekly (8/15/22), with a final strong warning on
    Dec 28. Returns how many notifications were created. The check
    itself is harmless on any other day (it creates nothing).
    """
    from datetime import datetime as _dt

    from sqlalchemy import select as _select

    from app.core.timezone import APPLICATION_TIMEZONE
    from app.database.connection import AsyncSessionLocal
    from app.modules.notifications.repo import create_notification
    from app.modules.system.retention_service import full_year_download_exists
    from app.modules.users.models import Role, User

    moment = now or _dt.now(APPLICATION_TIMEZONE)
    if moment.month != 12 or moment.day not in (1, 8, 15, 22, 28):
        return 0
    year = moment.year
    async with AsyncSessionLocal() as db:
        if await full_year_download_exists(db, year):
            return 0
        admins = (
            await db.execute(
                _select(User)
                .join(Role, User.role_id == Role.id)
                .where(
                    Role.name == "Super Admin",
                    User.is_active.is_(True),
                    User.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        final = moment.day == 28
        title = (
            f"FINAL WARNING: download the {year} full-year KPI report now"
            if final
            else f"Reminder: download the {year} full-year KPI report"
        )
        message = (
            f"The company-wide full-year KPI report for {year} has not been "
            "downloaded yet. Download it (Reports → window: Yearly, scope: "
            "Company-wide) and confirm the year's backup — departed "
            "employees' accounts cannot be permanently removed until both "
            "are done."
        )
        created = 0
        for admin in admins:
            await create_notification(
                db,
                {
                    "user_id": admin.id,
                    "title": title,
                    "message": message,
                    "notification_type": "YEARLY_REPORT_REMINDER",
                    "channel": "DASHBOARD",
                },
                commit=False,
            )
            created += 1
        await db.commit()
        return created


async def run_task_snapshot_purge_job(now=None) -> int:
    """Daily: purge departed task snapshots past their 1-year keep.

    Zenn's retention rule (2026-10-23): the snapshot-level task
    rows of a hard-deleted employee live one year after the hard
    delete (handover), then go automatically. KPI records/history
    are kept forever and are never touched by the purge.
    """
    now = now or _app_now()
    purged = 0
    async with async_session_maker() as db:
        from app.modules.system import retention_service as retention

        summaries = await retention.purge_expired_task_snapshots(
            db, today=now.date()
        )
        purged = sum(item["tasks_removed"] for item in summaries)
    if purged:
        logger.info(
            "Retention purge: removed %d expired task snapshots", purged
        )
    return purged


async def _locked_task_snapshot_purge():
    await _run_with_lock(
        "retention.task_snapshot_purge", run_task_snapshot_purge_job
    )


async def _locked_yearly_report_reminder():
    await _run_with_lock(
        "retention.yearly_report_reminder", run_yearly_report_reminder_job
    )


async def run_formula_lifecycle_job(now=None) -> dict:
    """Formula lifecycle (Zenn, 2026-10-24): warn, then auto-close.

    For every open formula WITH current period links (legacy
    unlinked formulas are never auto-closed):
    - 7 days before its last linked period ends, every Super Admin
      gets an in-app notification asking whether to continue —
      extend by linking the next period (the note says plainly when
      no later period exists yet).
    - Once that period has ended, the formula auto-closes:
      closed_at is stamped, its links become history, an audit row
      is written, and the Super Admins get the closing note.
    Date-exact conditions make the daily run idempotent.
    """
    from sqlalchemy import select as _select

    from app.core.timezone import as_application_time
    from app.models.audit_log import AuditLog
    from app.modules.kpi.models import (
        FormulaStatus,
        KPIFormula,
        KPIFormulaPeriod,
        KPIPeriod,
    )
    from app.modules.notifications.repo import create_notification
    from app.modules.users.models import Role, User

    moment = now or datetime.now(timezone)
    today = as_application_time(moment).date()
    warned = 0
    closed = 0
    async with AsyncSessionLocal() as db:
        admins = (
            await db.execute(
                _select(User)
                .join(Role, User.role_id == Role.id)
                .where(
                    Role.name == "Super Admin",
                    User.is_active.is_(True),
                    User.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        formulas = (
            await db.execute(
                _select(KPIFormula).where(
                    KPIFormula.status == FormulaStatus.ACTIVE,
                    KPIFormula.is_active.is_(True),
                    KPIFormula.is_deleted.is_(False),
                    KPIFormula.closed_at.is_(None),
                )
            )
        ).scalars().all()
        for formula in formulas:
            links = (
                await db.execute(
                    _select(KPIFormulaPeriod).where(
                        KPIFormulaPeriod.formula_id == formula.id,
                        KPIFormulaPeriod.is_current.is_(True),
                    )
                )
            ).scalars().all()
            if not links:
                continue
            periods = (
                await db.execute(
                    _select(KPIPeriod).where(
                        KPIPeriod.id.in_([link.period_id for link in links]),
                        KPIPeriod.is_deleted.is_(False),
                    )
                )
            ).scalars().all()
            if not periods:
                continue
            last_period = max(
                periods, key=lambda p: as_application_time(p.end_date)
            )
            end_date = as_application_time(last_period.end_date).date()
            days_left = (end_date - today).days
            if days_left == 7:
                later_exists = (
                    await db.execute(
                        _select(KPIPeriod.id)
                        .where(
                            KPIPeriod.is_deleted.is_(False),
                            KPIPeriod.end_date > last_period.end_date,
                        )
                        .limit(1)
                    )
                ).first() is not None
                message = (
                    f"KPI formula '{formula.name}' is linked to {last_period.label}, "
                    f"which ends on {end_date.isoformat()}. If it is not linked to a "
                    "later period, it will close after that day. To keep using it, "
                    "link the next period to the formula."
                )
                if not later_exists:
                    message += (
                        " No later period exists yet — create the next period "
                        "first, then link it."
                    )
                # Dedupe: one warning per formula per admin, even if
                # the job runs twice on the warning day.
                from app.modules.notifications.models import Notification

                already = (
                    await db.execute(
                        _select(Notification.id)
                        .where(
                            Notification.title == "KPI formula closing soon",
                            Notification.message == message,
                        )
                        .limit(1)
                    )
                ).first()
                if already is None:
                    for admin in admins:
                        await create_notification(db, {
                            "user_id": admin.id,
                            "title": "KPI formula closing soon",
                            "message": message,
                            "notification_type": "SYSTEM",
                            "channel": "DASHBOARD",
                        }, commit=False)
                    warned += 1
            elif days_left < 0:
                formula.closed_at = moment
                for link in links:
                    link.is_current = False
                db.add(AuditLog(
                    user_id=None, action="FORMULA_AUTO_CLOSED",
                    table_name="kpi_formulas", record_id=str(formula.id),
                    new_value={
                        "formula": formula.name,
                        "last_period": last_period.label,
                        "period_ended": end_date.isoformat(),
                    },
                ))
                for admin in admins:
                    await create_notification(db, {
                        "user_id": admin.id,
                        "title": "KPI formula closed",
                        "message": (
                            f"KPI formula '{formula.name}' closed because its last "
                            f"linked period ({last_period.label}) has ended. Its "
                            "rules are kept — revive it any time by linking a new "
                            "period."
                        ),
                        "notification_type": "SYSTEM",
                        "channel": "DASHBOARD",
                    }, commit=False)
                closed += 1
        await db.commit()
    if warned or closed:
        logger.info(
            "Formula lifecycle: warned %s, auto-closed %s", warned, closed
        )
    return {"warned": warned, "closed": closed}


async def _locked_formula_lifecycle():
    await _run_with_lock("formula.lifecycle", run_formula_lifecycle_job)


async def _run_sync_notification_job():
    await asyncio.to_thread(process_batch_notifications)


async def run_audit_log_retention_job():
    try:
        deleted_count = await run_audit_log_retention_cleanup()
        if deleted_count:
            logger.info("Deleted %s expired audit log records.", deleted_count)
    except Exception:
        logger.exception("Audit log retention cleanup failed.")
        raise


# ==============================================================================
# 1. KPI Rollup Tasks (Weekly & Monthly)
# ==============================================================================
async def generate_weekly_kpi():
    """လွန်ခဲ့သော တစ်ပတ်စာ Daily KPI များကို ပေါင်းချုပ်၍ Weekly KPI အဖြစ်ပြောင်းခြင်း"""
    logger.info("Starting Weekly KPI Rollup Task...")
    
    today = datetime.now(timezone).date()
    start_of_this_week = today - timedelta(days=today.weekday())
    start_of_last_week = start_of_this_week - timedelta(days=7)

    period_range = calendar_period_range(PeriodType.WEEKLY, start_of_last_week)
    period_start = period_range.start_inclusive
    period_end = period_range.end_exclusive

    async with AsyncSessionLocal() as db:
        try:
            # User အားလုံးကို ဆွဲထုတ်၍ Weekly KPI တွက်ချက်မည်
            stmt = select(User).where(
                User.is_active.is_(True),
                User.is_deleted.is_(False),
                User.id.in_(
                    select(UserDepartment.user_id).where(
                        UserDepartment.is_deleted.is_(False)
                    )
                ),
            )
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
    start_of_last_month = (first_day_of_this_month - timedelta(days=1)).replace(day=1)
    
    period_range = calendar_period_range(PeriodType.MONTHLY, start_of_last_month)
    period_start = period_range.start_inclusive
    period_end = period_range.end_exclusive

    async with AsyncSessionLocal() as db:
        try:
            # User အားလုံးကို ဆွဲထုတ်၍ Monthly KPI တွက်ချက်မည်
            stmt = select(User).where(
                User.is_active.is_(True),
                User.is_deleted.is_(False),
                User.id.in_(
                    select(UserDepartment.user_id).where(
                        UserDepartment.is_deleted.is_(False)
                    )
                ),
            )
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

    # Batch delivery is currently a no-op, so keep it opt-in until delivery exists.
    notifications_enabled = os.getenv(
        "ENABLE_BATCH_NOTIFICATIONS", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if notifications_enabled:
        scheduler.add_job(
            _locked_notifications,
            'interval',
            minutes=30,
            id='batch_notification_job',
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info("Batch notification no-op job registered by ENABLE_BATCH_NOTIFICATIONS.")
    else:
        logger.info(
            "Batch notification job is disabled; set ENABLE_BATCH_NOTIFICATIONS=true "
            "to register it (delivery is not implemented)."
        )

    scheduler.add_job(
        _locked_audit_log_retention,
        "cron",
        hour=3,
        minute=0,
        id="audit_log_retention_cleanup",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        _locked_yearly_report_reminder,
        "cron",
        month=12,
        day="1,8,15,22,28",
        hour=9,
        minute=0,
        id="yearly_report_reminder",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.add_job(
        _locked_task_snapshot_purge,
        "cron",
        hour=3,
        minute=30,
        id="task_snapshot_purge",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )


    scheduler.add_job(
        _locked_formula_lifecycle,
        "cron",
        hour=8,
        minute=30,
        id="formula_lifecycle",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.start()
    logger.info("🚀 System Scheduler (KPI & Workflow Engine) started successfully.")
    return scheduler
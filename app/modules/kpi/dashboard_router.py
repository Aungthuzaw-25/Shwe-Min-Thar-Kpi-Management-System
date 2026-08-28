from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Dict, Any
import pytz # 🌟 Added for Timezone Conversion
from app.core.timezone import APPLICATION_TIMEZONE # 🌟 Added for Timezone Conversion

from app.database.connection import get_db
from app.modules.users.models import User
from app.modules.departments.models import Department
from app.modules.notifications.models import Notification
from app.modules.kpi.models import KPIRecord, KPIStatus

# 🌟 လုံခြုံရေးအတွက် Role Checker ကို Import လုပ်ခြင်း
from app.modules.users.router import admin_checker 

router = APIRouter()

@router.get("/summary", tags=["Super Admin Dashboard"])
async def fetch_superadmin_dashboard_data(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_checker) # 🌟 ဤနေရာတွင် Token နှင့် Role ကို စစ်ဆေးမည်
) -> Dict[str, Any]:
    
    # 1. Total Active Users
    users_count = await db.execute(select(func.count(User.id)).where(User.is_active == True))
    total_users = users_count.scalar() or 0

    # 2. Total Departments
    depts_count = await db.execute(select(func.count(Department.id)))
    total_depts = depts_count.scalar() or 0

    # 3. Active Alerts (Unread Notifications)
    alerts_count = await db.execute(select(func.count(Notification.id)).where(Notification.is_read == False))
    active_alerts = alerts_count.scalar() or 0

    # 4. Total KPIs Evaluated
    kpis_count = await db.execute(select(func.count(KPIRecord.id)))
    total_kpis = kpis_count.scalar() or 0

    # 5. Completed KPIs
    completed_kpis_stmt = select(func.count(KPIRecord.id)).where(
        KPIRecord.status_bucket.in_([KPIStatus.EXCELLENT, KPIStatus.VERY_GOOD])
    )
    completed_kpis_count = await db.execute(completed_kpis_stmt)
    completed_kpis = completed_kpis_count.scalar() or 0

    # =========================================================
    # 🌟 6. Recent Alerts List (BUG & TIMEZONE FIXES)
    # =========================================================
    recent_alerts_stmt = (
        select(Notification, User)
        .outerjoin(User, Notification.user_id == User.id)
        .where(Notification.is_read == False)
        .order_by(Notification.created_at.desc())
        .limit(6)
    )
    recent_alerts_req = await db.execute(recent_alerts_stmt)
    
    yangon_tz = APPLICATION_TIMEZONE
    recent_alerts = []
    
    for n, u in recent_alerts_req.all():
        # 🛡️ Convert Database Naive UTC to Asia/Yangon Local Time
        local_time = n.created_at.replace(tzinfo=pytz.utc).astimezone(yangon_tz)
        
        # 🛡️ Extract actual user name or fallback to System
        user_name = f"{u.first_name} {u.last_name}" if u else "System Alert"
        
        recent_alerts.append({
            "name": user_name,
            "title": n.title, 
            "message": n.message, # 🛡️ Ensures detailed context is sent to UI
            "time": local_time.strftime("%d %b %Y, %I:%M %p")
        })

    # 7. Top Performing Departments
    top_depts_stmt = (
        select(
            Department.name,
            func.avg(KPIRecord.percentage).label("avg_completion")
        )
        .join(KPIRecord, Department.id == KPIRecord.department_id)
        .group_by(Department.name)
        .order_by(func.avg(KPIRecord.percentage).desc())
        .limit(5)
    )
    top_depts_req = await db.execute(top_depts_stmt)
    
    top_departments = []
    for row in top_depts_req.all():
        top_departments.append({
            "name": row[0],
            "completion": round(row[1] or 0.0, 1),
            "avg_score": round(row[1] or 0.0, 2),
            "trend": 0.0 
        })

    trend_stmt = (
        select(
            func.date(KPIRecord.period_start).label("period"),
            func.avg(KPIRecord.percentage).label("average")
        )
        .where(KPIRecord.period_start.is_not(None))
        .group_by(func.date(KPIRecord.period_start))
        .order_by(func.date(KPIRecord.period_start).desc())
        .limit(7)
    )
    trend_rows = (await db.execute(trend_stmt)).all()
    trend_rows.reverse()
    trend = {
        "labels": [row.period.strftime("%d %b") if hasattr(row.period, "strftime") else str(row.period) for row in trend_rows],
        "data": [round(float(row.average or 0.0), 2) for row in trend_rows],
    }

    return {
        "metrics": {
            "total_users": total_users,
            "total_departments": total_depts,
            "total_kpis": total_kpis, 
            "completed_kpis": completed_kpis, 
            "active_alerts": active_alerts
        },
        "recent_alerts": recent_alerts,
        "top_departments": top_departments,
        "trend": trend
    }
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.connection import get_db

# 🌟 FIX: Import the module-level functions from repo.py
from app.modules.notifications.repo import (
    count_unread_notifications,
    get_notification_by_user,
    get_notifications_by_user,
    mark_all_notifications_as_read,
    mark_notification_as_read,
)
from app.modules.notifications.schemas import NotificationResponse

# 🌟 Swagger UI တွင် သပ်ရပ်စွာပေါ်ရန် Tag ထည့်ပေးခြင်း
router = APIRouter(tags=["Notifications"])
NOTIFICATION_NOT_FOUND = "Notification not found"


@router.get("/unread-count")
async def get_unread_count(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return {"count": await count_unread_notifications(db, current_user.id)}

@router.get("", response_model=list[NotificationResponse])
async def get_my_notifications(
    skip: int = Query(0, ge=0),
    # 🌟 SECURITY FIX: Prevent Memory Exhaustion DoS by capping limit to 100
    limit: int = Query(50, gt=0, le=100, description="Maximum number of notifications to fetch (capped at 100)"),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """မိမိ၏ Notification History များအားလုံးကို ကြည့်ရှုရန်"""
    # 🌟 FIX: Call the function directly
    return await get_notifications_by_user(db, current_user.id, skip=skip, limit=limit)

@router.get("/{notification_id}", response_model=NotificationResponse)
async def get_my_notification(
    notification_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Return one notification only when it belongs to the authenticated user."""
    try:
        parsed_notification_id = UUID(notification_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=NOTIFICATION_NOT_FOUND,
        ) from None
    notification = await get_notification_by_user(db, parsed_notification_id, current_user.id)
    if notification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOTIFICATION_NOT_FOUND)
    if not notification.is_read:
        notification.is_read = True
        await db.commit()
        await db.refresh(notification)
    return notification

# 🌟 အရေးကြီး: /read-all ကို UUID အထက်တွင် ရေးရပါမည်
@router.put("/read-all", status_code=status.HTTP_200_OK)
async def mark_all_as_read(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Notification အားလုံးကို တစ်ပြိုင်တည်း ဖတ်ပြီး (Read) အဖြစ်ပြောင်းရန်"""
    # 🌟 FIX: Call the function directly
    updated_count = await mark_all_notifications_as_read(db, current_user.id)
    return {"success": True, "message": f"{updated_count} notifications marked as read"}

# 🌟 Dynamic Parameter (UUID) ကို အောက်ဆုံးတွင် ထားရပါမည်
@router.put("/{notification_id}/read", status_code=status.HTTP_200_OK)
async def mark_as_read(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """တိကျသော Notification တစ်ခုအား ဖတ်ပြီး (Read) အဖြစ်ပြောင်းရန်"""
    # 🌟 FIX: Call the function directly
    success = await mark_notification_as_read(db, notification_id, current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail=NOTIFICATION_NOT_FOUND)
    return {"success": True, "message": "Marked as read"}
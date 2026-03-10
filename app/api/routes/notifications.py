"""
Superadmin — Notifications routes.

Create, manage, and broadcast announcements and maintenance notices.

Register in main.py:
    from app.api.routes.notifications import router as notifications_router
    app.include_router(notifications_router, prefix="/api/v1/superadmin", tags=["superadmin-notifications"])
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.database import Notification, AuditLog
from app.models.schemas import (
    NotificationCreate, NotificationItem, NotificationListResponse,
)
from app.api.dependencies import require_superadmin

logger = logging.getLogger("api.superadmin.notifications")
router = APIRouter()


async def _log_action(db, actor, action, resource_type, resource_id=None, details=None, request=None):
    db.add(AuditLog(
        actor_id=actor["id"], actor_email=actor["email"], action=action,
        resource_type=resource_type, resource_id=str(resource_id) if resource_id else None,
        details=details, ip_address=request.client.host if request and request.client else None,
    ))


def _format(n: Notification) -> NotificationItem:
    return NotificationItem(
        id=str(n.id), title=n.title, message=n.message,
        notification_type=n.notification_type, target=n.target,
        target_tenant_id=str(n.target_tenant_id) if n.target_tenant_id else None,
        is_active=n.is_active, created_by=n.created_by,
        created_at=n.created_at, scheduled_at=n.scheduled_at,
    )


@router.get("/notifications", response_model=NotificationListResponse)
async def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    notification_type: Optional[str] = None,
    is_active: Optional[bool] = None,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """List all notifications."""
    filters = []
    if notification_type:
        filters.append(Notification.notification_type == notification_type)
    if is_active is not None:
        filters.append(Notification.is_active == is_active)

    count_q = select(func.count(Notification.id))
    if filters:
        count_q = count_q.where(*filters)
    total = (await db.execute(count_q)).scalar() or 0

    query = select(Notification).order_by(desc(Notification.created_at)).offset((page - 1) * page_size).limit(page_size)
    if filters:
        query = query.where(*filters)

    result = await db.execute(query)
    items = result.scalars().all()

    return NotificationListResponse(
        notifications=[_format(n) for n in items],
        total=total, page=page, page_size=page_size,
    )


@router.post("/notifications", response_model=NotificationItem, status_code=201)
async def create_notification(
    body: NotificationCreate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new notification."""
    notif = Notification(
        title=body.title.strip(),
        message=body.message.strip(),
        notification_type=body.notification_type,
        target=body.target,
        target_tenant_id=body.target_tenant_id,
        is_active=True,
        created_by=admin["email"],
        scheduled_at=body.scheduled_at,
    )
    db.add(notif)

    await _log_action(db, admin, "notification.create", "notification", details={
        "title": notif.title, "type": notif.notification_type, "target": notif.target,
    }, request=request)

    await db.commit()
    await db.refresh(notif)
    return _format(notif)


@router.patch("/notifications/{notif_id}/toggle")
async def toggle_notification(
    notif_id: str,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Toggle a notification active/inactive."""
    result = await db.execute(select(Notification).where(Notification.id == notif_id))
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")

    notif.is_active = not notif.is_active
    await _log_action(db, admin, "notification.toggle", "notification", notif_id, {
        "is_active": notif.is_active,
    }, request)
    await db.commit()

    return {"id": str(notif.id), "is_active": notif.is_active}


@router.delete("/notifications/{notif_id}")
async def delete_notification(
    notif_id: str,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a notification."""
    result = await db.execute(select(Notification).where(Notification.id == notif_id))
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")

    await _log_action(db, admin, "notification.delete", "notification", notif_id, {
        "title": notif.title,
    }, request)

    await db.delete(notif)
    await db.commit()
    return {"deleted": True}

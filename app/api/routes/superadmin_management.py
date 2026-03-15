"""
Superadmin — Account Management routes.

Allows existing superadmins to create, list, update, and deactivate
other superadmin accounts. The initial setup endpoint (one-time) remains
in superadmin.py — this module handles ongoing management.

Register in main.py:
    from app.api.routes.superadmin_management import router as superadmin_mgmt_router
    app.include_router(superadmin_mgmt_router, prefix="/api/v1/superadmin/admins", tags=["superadmin-management"])
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.api.dependencies import require_superadmin
from app.core.security import hash_password, verify_password
from app.models.database import SuperAdmin, AuditLog
from app.models.schemas import (
    SuperAdminCreate,
    SuperAdminUpdate,
    SuperAdminPasswordChange,
    SuperAdminStatusUpdate,
    SuperAdminListItem,
    SuperAdminListResponse,
    SuperAdminProfile,
)

logger = logging.getLogger("api.superadmin.management")
router = APIRouter()


async def _log_action(
    db: AsyncSession, actor: dict, action: str,
    resource_type: str, resource_id: str = None,
    details: dict = None, request: Request = None,
):
    entry = AuditLog(
        actor_id=actor["id"],
        actor_email=actor["email"],
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        details=details,
        ip_address=request.client.host if request and request.client else None,
    )
    db.add(entry)


def _format_admin(sa: SuperAdmin) -> SuperAdminListItem:
    return SuperAdminListItem(
        id=str(sa.id),
        email=sa.email,
        name=sa.name,
        is_active=sa.is_active,
        last_login_at=sa.last_login_at,
        created_at=sa.created_at,
    )


# ═══════════════════════════════════════════════════════════════════════════
# List Superadmins
# ═══════════════════════════════════════════════════════════════════════════

@router.get("", response_model=SuperAdminListResponse)
async def list_superadmins(
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """List all superadmin accounts."""
    query = select(SuperAdmin).order_by(SuperAdmin.created_at.asc())

    filters = []
    if search:
        filters.append(
            SuperAdmin.email.ilike(f"%{search}%") | SuperAdmin.name.ilike(f"%{search}%")
        )
    if is_active is not None:
        filters.append(SuperAdmin.is_active == is_active)

    if filters:
        query = query.where(*filters)

    result = await db.execute(query)
    admins = result.scalars().all()

    total_q = select(func.count(SuperAdmin.id))
    if filters:
        total_q = total_q.where(*filters)
    total = (await db.execute(total_q)).scalar() or 0

    return SuperAdminListResponse(
        admins=[_format_admin(a) for a in admins],
        total=total,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Get Single Superadmin
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/{admin_id}", response_model=SuperAdminProfile)
async def get_superadmin(
    admin_id: str,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific superadmin's profile."""
    result = await db.execute(select(SuperAdmin).where(SuperAdmin.id == admin_id))
    sa = result.scalar_one_or_none()
    if not sa:
        raise HTTPException(status_code=404, detail="Superadmin not found")

    return SuperAdminProfile(
        id=str(sa.id),
        email=sa.email,
        name=sa.name,
        is_active=sa.is_active,
        last_login_at=sa.last_login_at,
        created_at=sa.created_at,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Create Superadmin
# ═══════════════════════════════════════════════════════════════════════════

@router.post("", response_model=SuperAdminListItem, status_code=201)
async def create_superadmin(
    body: SuperAdminCreate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new superadmin account."""
    # Check for existing email
    existing = await db.execute(
        select(SuperAdmin).where(SuperAdmin.email == body.email.lower().strip())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Email '{body.email}' is already in use")

    new_admin = SuperAdmin(
        email=body.email.lower().strip(),
        name=body.name.strip(),
        password_hash=hash_password(body.password),
        is_active=True,
    )
    db.add(new_admin)

    await _log_action(db, admin, "superadmin.create", "superadmin", details={
        "created_email": new_admin.email,
        "created_name": new_admin.name,
    }, request=request)

    await db.commit()
    await db.refresh(new_admin)

    logger.info(f"Superadmin created: {new_admin.email} by {admin['email']}")
    return _format_admin(new_admin)


# ═══════════════════════════════════════════════════════════════════════════
# Update Superadmin
# ═══════════════════════════════════════════════════════════════════════════

@router.put("/{admin_id}", response_model=SuperAdminListItem)
async def update_superadmin(
    admin_id: str,
    body: SuperAdminUpdate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Update a superadmin's name or email."""
    result = await db.execute(select(SuperAdmin).where(SuperAdmin.id == admin_id))
    sa = result.scalar_one_or_none()
    if not sa:
        raise HTTPException(status_code=404, detail="Superadmin not found")

    changes = {}
    if body.name is not None:
        changes["name"] = body.name.strip()
        sa.name = body.name.strip()
    if body.email is not None:
        new_email = body.email.lower().strip()
        # Check uniqueness
        dup = await db.execute(
            select(SuperAdmin).where(SuperAdmin.email == new_email, SuperAdmin.id != admin_id)
        )
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Email '{new_email}' is already in use")
        changes["email"] = new_email
        sa.email = new_email

    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")

    sa.updated_at = datetime.utcnow()

    await _log_action(db, admin, "superadmin.update", "superadmin", str(sa.id), {
        "changes": changes,
        "target_email": sa.email,
    }, request)

    await db.commit()
    await db.refresh(sa)
    return _format_admin(sa)


# ═══════════════════════════════════════════════════════════════════════════
# Change Password
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/{admin_id}/change-password")
async def change_password(
    admin_id: str,
    body: SuperAdminPasswordChange,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Change a superadmin's password. Requires current password if changing own."""
    result = await db.execute(select(SuperAdmin).where(SuperAdmin.id == admin_id))
    sa = result.scalar_one_or_none()
    if not sa:
        raise HTTPException(status_code=404, detail="Superadmin not found")

    # If changing own password, verify current password
    if str(admin["id"]) == admin_id:
        if not verify_password(body.current_password, sa.password_hash):
            raise HTTPException(status_code=400, detail="Current password is incorrect")

    sa.password_hash = hash_password(body.new_password)
    sa.updated_at = datetime.utcnow()

    await _log_action(db, admin, "superadmin.password_change", "superadmin", str(sa.id), {
        "target_email": sa.email,
        "self_change": str(admin["id"]) == admin_id,
    }, request)

    await db.commit()

    return {"message": "Password updated successfully"}


# ═══════════════════════════════════════════════════════════════════════════
# Toggle Active Status
# ═══════════════════════════════════════════════════════════════════════════

@router.patch("/{admin_id}/status", response_model=SuperAdminListItem)
async def toggle_superadmin_status(
    admin_id: str,
    body: SuperAdminStatusUpdate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Activate or deactivate a superadmin account."""
    # Cannot deactivate yourself
    if str(admin["id"]) == admin_id and not body.is_active:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    result = await db.execute(select(SuperAdmin).where(SuperAdmin.id == admin_id))
    sa = result.scalar_one_or_none()
    if not sa:
        raise HTTPException(status_code=404, detail="Superadmin not found")

    # Prevent deactivating the last active superadmin
    if not body.is_active:
        active_count = (await db.execute(
            select(func.count(SuperAdmin.id)).where(
                SuperAdmin.is_active == True,
                SuperAdmin.id != admin_id,
            )
        )).scalar() or 0
        if active_count == 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot deactivate the last active superadmin"
            )

    sa.is_active = body.is_active
    sa.updated_at = datetime.utcnow()

    await _log_action(db, admin, "superadmin.status_change", "superadmin", str(sa.id), {
        "target_email": sa.email,
        "new_status": "active" if body.is_active else "deactivated",
    }, request)

    await db.commit()
    await db.refresh(sa)
    return _format_admin(sa)
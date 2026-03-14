"""
Superadmin — Staff User Management routes.

Manage staff users across any tenant. All actions are audit-logged.

Register in main.py:
    from app.api.routes.staff_management import router as staff_mgmt_router
    app.include_router(staff_mgmt_router, prefix="/api/v1/superadmin/tenants", tags=["superadmin-staff"])
"""

import logging
import uuid
from typing import Optional

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.database import (
    StaffUser, Tenant, UserRole, AuditLog,
)
from app.models.schemas import (
    StaffCreate, StaffUpdate, StaffStatusUpdate,
    StaffListItem, StaffListResponse,
)
from app.api.dependencies import require_superadmin
from app.services.auth0_mgmt import Auth0ManagementService

logger = logging.getLogger("api.superadmin.staff")
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────

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


async def _get_tenant_or_404(db: AsyncSession, tenant_id: str) -> Tenant:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


def _format_staff(s: StaffUser) -> StaffListItem:
    return StaffListItem(
        id=str(s.id),
        tenant_id=str(s.tenant_id),
        email=s.email,
        name=s.name,
        role=s.role.value if hasattr(s.role, "value") else str(s.role),
        is_active=s.is_active,
        auth0_user_id=s.auth0_user_id,
        last_login_at=s.last_login_at,
        created_at=s.created_at,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Staff CRUD (scoped to a tenant)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/{tenant_id}/staff", response_model=StaffListResponse)
async def list_staff(
    tenant_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    role: Optional[str] = Query(None, pattern=r"^(admin|staff)$"),
    is_active: Optional[bool] = None,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """List staff users for a specific tenant."""
    await _get_tenant_or_404(db, tenant_id)

    filters = [StaffUser.tenant_id == tenant_id]
    if search:
        filters.append(
            (StaffUser.email.ilike(f"%{search}%")) | (StaffUser.name.ilike(f"%{search}%"))
        )
    if role:
        filters.append(StaffUser.role == UserRole(role))
    if is_active is not None:
        filters.append(StaffUser.is_active == is_active)

    count_q = select(func.count(StaffUser.id)).where(*filters)
    total = (await db.execute(count_q)).scalar() or 0

    query = (
        select(StaffUser)
        .where(*filters)
        .order_by(desc(StaffUser.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    staff_list = result.scalars().all()

    return StaffListResponse(
        staff=[_format_staff(s) for s in staff_list],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{tenant_id}/staff", status_code=201)
async def create_staff(
    tenant_id: str,
    body: StaffCreate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new staff user for a tenant."""
    await _get_tenant_or_404(db, tenant_id)

    # Check email uniqueness within tenant
    existing = await db.execute(
        select(StaffUser).where(
            StaffUser.tenant_id == tenant_id,
            StaffUser.email == body.email.lower().strip(),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Staff with email '{body.email}' already exists in this tenant")

    # Auto-create user in Auth0
    auth0_svc = Auth0ManagementService()
    auth0_result = await auth0_svc.create_user(
        email=body.email.lower().strip(),
        name=body.name.strip(),
    )

    staff = StaffUser(
        tenant_id=tenant_id,
        email=body.email.lower().strip(),
        name=body.name.strip(),
        role=UserRole(body.role),
        auth0_user_id=auth0_result["auth0_user_id"],
        is_active=True,
    )
    db.add(staff)

    await _log_action(db, admin, "staff.create", "staff_user", details={
        "tenant_id": tenant_id,
        "email": staff.email,
        "role": body.role,
        "auth0_auto_created": auth0_result["auto_created"],
    }, request=request)

    await db.commit()
    await db.refresh(staff)

    result = _format_staff(staff)
    response = result.model_dump() if hasattr(result, "model_dump") else dict(result)

    if auth0_result.get("password_reset_url"):
        response["password_reset_url"] = auth0_result["password_reset_url"]
    response["auth0_auto_created"] = auth0_result.get("auto_created", False)

    return response


@router.get("/{tenant_id}/staff/{staff_id}", response_model=StaffListItem)
async def get_staff(
    tenant_id: str,
    staff_id: str,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Get a single staff user's details."""
    result = await db.execute(
        select(StaffUser).where(
            StaffUser.id == staff_id,
            StaffUser.tenant_id == tenant_id,
        )
    )
    staff = result.scalar_one_or_none()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff user not found")

    return _format_staff(staff)


@router.put("/{tenant_id}/staff/{staff_id}", response_model=StaffListItem)
async def update_staff(
    tenant_id: str,
    staff_id: str,
    body: StaffUpdate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Update a staff user's name, role, or email."""
    result = await db.execute(
        select(StaffUser).where(
            StaffUser.id == staff_id,
            StaffUser.tenant_id == tenant_id,
        )
    )
    staff = result.scalar_one_or_none()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff user not found")

    changes = {}
    if body.name is not None and body.name.strip():
        changes["name"] = {"from": staff.name, "to": body.name}
        staff.name = body.name.strip()
    if body.role is not None:
        changes["role"] = {"from": staff.role.value if hasattr(staff.role, "value") else str(staff.role), "to": body.role}
        staff.role = UserRole(body.role)
    if body.email is not None:
        new_email = body.email.lower().strip()
        # Check uniqueness
        dup = await db.execute(
            select(StaffUser).where(
                StaffUser.tenant_id == tenant_id,
                StaffUser.email == new_email,
                StaffUser.id != staff_id,
            )
        )
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Email '{new_email}' already in use")
        changes["email"] = {"from": staff.email, "to": new_email}
        staff.email = new_email

    if changes:
        await _log_action(db, admin, "staff.update", "staff_user", staff_id, {
            "tenant_id": tenant_id, **changes,
        }, request)
        await db.commit()
        await db.refresh(staff)
    # Sync changes to Auth0
    if changes and not staff.auth0_user_id.startswith("pending|"):
        auth0_svc = Auth0ManagementService()
        await auth0_svc.update_user(
            auth0_user_id=staff.auth0_user_id,
            name=staff.name if "name" in changes else None,
            email=staff.email if "email" in changes else None,
        )

    return _format_staff(staff)


@router.patch("/{tenant_id}/staff/{staff_id}/status")
async def update_staff_status(
    tenant_id: str,
    staff_id: str,
    body: StaffStatusUpdate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Activate or deactivate a staff user."""
    result = await db.execute(
        select(StaffUser).where(
            StaffUser.id == staff_id,
            StaffUser.tenant_id == tenant_id,
        )
    )
    staff = result.scalar_one_or_none()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff user not found")

    old_status = staff.is_active
    staff.is_active = body.is_active

    await _log_action(db, admin, "staff.status_change", "staff_user", staff_id, {
        "tenant_id": tenant_id,
        "email": staff.email,
        "from": old_status,
        "to": body.is_active,
    }, request)

    await db.commit()

    return {
        "id": str(staff.id),
        "email": staff.email,
        "is_active": staff.is_active,
        "previous_status": old_status,
    }

@router.delete("/{tenant_id}/staff/{staff_id}")
async def delete_staff(
    tenant_id: str,
    staff_id: str,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """
    Soft-delete a staff user. Preserves their name for history display,
    deactivates the account, and deletes the Auth0 user.
    """
    result = await db.execute(
        select(StaffUser).where(
            StaffUser.id == staff_id,
            StaffUser.tenant_id == tenant_id,
        )
    )
    staff = result.scalar_one_or_none()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff user not found")

    if staff.deleted_at:
        raise HTTPException(status_code=400, detail="Staff user is already deleted")

    # Preserve name for history, mark as deleted
    staff.deleted_name = staff.name
    staff.deleted_at = datetime.utcnow()
    staff.is_active = False
    staff.name = f"{staff.name} (Deleted)"

    # Delete from Auth0
    auth0_svc = Auth0ManagementService()
    auth0_deleted = await auth0_svc.delete_user(staff.auth0_user_id)

    await _log_action(db, admin, "staff.delete", "staff_user", staff_id, {
        "tenant_id": tenant_id,
        "email": staff.email,
        "deleted_name": staff.deleted_name,
        "auth0_deleted": auth0_deleted,
    }, request)

    await db.commit()

    return {
        "id": str(staff.id),
        "email": staff.email,
        "deleted": True,
        "auth0_deleted": auth0_deleted,
        "message": f"Staff user '{staff.deleted_name}' has been deleted.",
    }
@router.post("/{tenant_id}/staff/{staff_id}/reset-password")
async def reset_staff_password(
    tenant_id: str,
    staff_id: str,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new password reset link for an existing staff user."""
    result = await db.execute(
        select(StaffUser).where(
            StaffUser.id == staff_id,
            StaffUser.tenant_id == tenant_id,
        )
    )
    staff = result.scalar_one_or_none()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff user not found")

    if not staff.is_active:
        raise HTTPException(status_code=400, detail="Cannot reset password for inactive user")

    if staff.auth0_user_id.startswith("pending|"):
        raise HTTPException(status_code=400, detail="User has not been provisioned in Auth0 yet")

    auth0_svc = Auth0ManagementService()
    token = await auth0_svc._get_mgmt_token()
    if not token:
        raise HTTPException(status_code=500, detail="Auth0 Management API unavailable")

    import httpx
    async with httpx.AsyncClient() as client:
        ticket_resp = await client.post(
            f"https://{auth0_svc.domain}/api/v2/tickets/password-change",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "user_id": staff.auth0_user_id,
                "result_url": "https://agencylensai.com/auth",
                "ttl_sec": 604800,
            },
            timeout=10,
        )

    if ticket_resp.status_code != 201:
        raise HTTPException(status_code=500, detail="Failed to generate password reset link")

    password_reset_url = ticket_resp.json().get("ticket")

    await _log_action(db, admin, "staff.password_reset", "staff_user", staff_id, {
        "tenant_id": tenant_id,
        "email": staff.email,
    }, request)
    await db.commit()

    return {
        "password_reset_url": password_reset_url,
        "email": staff.email,
        "message": f"Password reset link generated for {staff.email}. Valid for 7 days.",
    }
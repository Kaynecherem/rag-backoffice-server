"""
Superadmin — Impersonation ("View As") routes.

Generates valid staff or policyholder tokens so the superadmin can
switch into the main app's frontend as any user. All impersonation
actions are audit-logged.

The tokens are created using the SAME secret key and format as the
main app, so they work directly in the client frontend.

UPDATED: Response now includes tenant_slug so the frontend can build
the correct subdomain URL (e.g., levanti.agencylensai.com).

Register in main.py:
    from app.api.routes.impersonation import router as impersonation_router
    app.include_router(impersonation_router, prefix="/api/v1/superadmin/tenants", tags=["superadmin-impersonation"])
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.config import get_settings
from app.models.database import (
    Tenant, StaffUser, Policyholder, AuditLog, UserRole,
)
from app.models.schemas import (
    ImpersonateStaffRequest, ImpersonatePolicyholderRequest, ImpersonationToken,
)
from app.api.dependencies import require_superadmin

logger = logging.getLogger("api.superadmin.impersonation")
router = APIRouter()
settings = get_settings()

ALGORITHM = "HS256"
IMPERSONATION_HOURS = 2  # Short-lived for safety


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


# ═══════════════════════════════════════════════════════════════════════════
# Impersonate Staff
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/{tenant_id}/impersonate/staff", response_model=ImpersonationToken)
async def impersonate_staff(
    tenant_id: str,
    body: ImpersonateStaffRequest,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a staff token for the main app. Allows the superadmin
    to log into the client frontend as a staff member.
    """
    # Verify tenant
    t_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = t_result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Find staff user
    if body.staff_id:
        s_result = await db.execute(
            select(StaffUser).where(
                StaffUser.id == body.staff_id,
                StaffUser.tenant_id == tenant_id,
                StaffUser.is_active == True,
            )
        )
    else:
        # Default: first active admin, or first active staff
        s_result = await db.execute(
            select(StaffUser)
            .where(
                StaffUser.tenant_id == tenant_id,
                StaffUser.is_active == True,
            )
            .order_by(
                # Prefer admins
                StaffUser.role.asc(),
                StaffUser.created_at.asc(),
            )
            .limit(1)
        )

    staff = s_result.scalar_one_or_none()
    if not staff:
        raise HTTPException(status_code=404, detail="No active staff user found in this tenant")

    # Create a token matching the main app's staff token format
    payload = {
        "sub": str(staff.id),
        "tenant_id": str(tenant_id),
        "email": staff.email,
        "role": body.role,
        "type": "staff_session",
        "impersonated_by": admin["email"],
        "exp": datetime.utcnow() + timedelta(hours=IMPERSONATION_HOURS),
        "iat": datetime.utcnow(),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)

    await _log_action(db, admin, "impersonation.staff", "staff_user", str(staff.id), {
        "tenant_id": tenant_id,
        "tenant_name": tenant.name,
        "staff_email": staff.email,
        "role": body.role,
        "expires_hours": IMPERSONATION_HOURS,
    }, request)
    await db.commit()

    return ImpersonationToken(
        token=token,
        impersonating="staff",
        tenant_id=str(tenant_id),
        tenant_name=tenant.name,
        tenant_slug=tenant.slug,
        user_identifier=staff.email,
        role=body.role,
        expires_in_hours=IMPERSONATION_HOURS,
        impersonator_name=admin.get("name", admin["email"]),
        notice=f"Impersonation token for {staff.email} ({body.role}). "
               f"Valid for {IMPERSONATION_HOURS} hours. All actions are logged.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Impersonate Policyholder
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/{tenant_id}/impersonate/policyholder", response_model=ImpersonationToken)
async def impersonate_policyholder(
    tenant_id: str,
    body: ImpersonatePolicyholderRequest,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a policyholder token for the main app.
    """
    # Verify tenant
    t_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = t_result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Find policyholder by policy number
    ph_result = await db.execute(
        select(Policyholder).where(
            Policyholder.tenant_id == tenant_id,
            Policyholder.policy_number == body.policy_number,
            Policyholder.is_active == True,
        )
    )
    ph = ph_result.scalar_one_or_none()
    if not ph:
        raise HTTPException(status_code=404, detail=f"No active policyholder found with policy {body.policy_number}")

    payload = {
        "sub": str(ph.id),
        "tenant_id": str(tenant_id),
        "policy_number": ph.policy_number,
        "type": "policyholder_session",
        "impersonated_by": admin["email"],
        "exp": datetime.utcnow() + timedelta(hours=IMPERSONATION_HOURS),
        "iat": datetime.utcnow(),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)

    await _log_action(db, admin, "impersonation.policyholder", "policyholder", str(ph.id), {
        "tenant_id": tenant_id,
        "tenant_name": tenant.name,
        "policy_number": ph.policy_number,
        "expires_hours": IMPERSONATION_HOURS,
    }, request)
    await db.commit()

    return ImpersonationToken(
        token=token,
        impersonating="policyholder",
        tenant_id=str(tenant_id),
        tenant_name=tenant.name,
        tenant_slug=tenant.slug,
        user_identifier=ph.policy_number,
        role="policyholder",
        expires_in_hours=IMPERSONATION_HOURS,
        impersonator_name=admin.get("name", admin["email"]),
        notice=f"Impersonation token for policyholder {ph.policy_number}. "
               f"Valid for {IMPERSONATION_HOURS} hours. All actions are logged.",
    )
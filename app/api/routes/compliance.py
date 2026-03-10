"""
Superadmin — Disclaimer & Compliance routes.

Per-tenant disclaimer management with acceptance tracking.

Register in main.py:
    from app.api.routes.compliance import router as compliance_router
    app.include_router(compliance_router, prefix="/api/v1/superadmin/tenants", tags=["superadmin-compliance"])
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.database import Tenant, AuditLog
from app.models.schemas import DisclaimerConfig, DisclaimerUpdate
from app.api.dependencies import require_superadmin

logger = logging.getLogger("api.superadmin.compliance")
router = APIRouter()

DEFAULT_DISCLAIMER = (
    "This assistant provides information based on your insurance policy documents for informational "
    "purposes only. It does not constitute professional insurance advice, and should not be relied upon "
    "for coverage decisions. For binding interpretations, claims, or policy changes, please contact your "
    "insurance agent directly."
)


@router.get("/{tenant_id}/disclaimer", response_model=DisclaimerConfig)
async def get_disclaimer(
    tenant_id: str,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Get disclaimer config for a tenant."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    wc = tenant.widget_config or {}

    return DisclaimerConfig(
        tenant_id=str(tenant.id),
        tenant_name=tenant.name,
        disclaimer_text=wc.get("disclaimer_text", DEFAULT_DISCLAIMER),
        disclaimer_enabled=wc.get("disclaimer_enabled", True),
        acceptance_count=wc.get("disclaimer_acceptance_count", 0),
        last_updated=wc.get("disclaimer_updated_at"),
    )


@router.put("/{tenant_id}/disclaimer", response_model=DisclaimerConfig)
async def update_disclaimer(
    tenant_id: str,
    body: DisclaimerUpdate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Update disclaimer text and/or enabled status."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    wc = tenant.widget_config or {}
    updates = body.model_dump(exclude_none=True)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    from datetime import datetime
    if "disclaimer_text" in updates:
        wc["disclaimer_text"] = updates["disclaimer_text"]
    if "disclaimer_enabled" in updates:
        wc["disclaimer_enabled"] = updates["disclaimer_enabled"]
    wc["disclaimer_updated_at"] = datetime.utcnow().isoformat()

    tenant.widget_config = wc

    db.add(AuditLog(
        actor_id=admin["id"], actor_email=admin["email"],
        action="compliance.disclaimer_update", resource_type="tenant",
        resource_id=tenant_id,
        details={"fields_updated": list(updates.keys())},
        ip_address=request.client.host if request and request.client else None,
    ))

    await db.commit()
    await db.refresh(tenant)

    return DisclaimerConfig(
        tenant_id=str(tenant.id),
        tenant_name=tenant.name,
        disclaimer_text=wc.get("disclaimer_text", DEFAULT_DISCLAIMER),
        disclaimer_enabled=wc.get("disclaimer_enabled", True),
        acceptance_count=wc.get("disclaimer_acceptance_count", 0),
        last_updated=wc.get("disclaimer_updated_at"),
    )

"""
Superadmin — Onboarding Workflow routes.

Step-by-step tenant setup checklist tracked per tenant.

Register in main.py:
    from app.api.routes.onboarding import router as onboarding_router
    app.include_router(onboarding_router, prefix="/api/v1/superadmin/tenants", tags=["superadmin-onboarding"])
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.database import Tenant, StaffUser, Policyholder, Document, AuditLog
from app.models.schemas import OnboardingStatus, OnboardingStepUpdate
from app.api.dependencies import require_superadmin

logger = logging.getLogger("api.superadmin.onboarding")
router = APIRouter()

# ── Onboarding step definitions ──────────────────────────────────────────

ONBOARDING_STEPS = [
    {"key": "tenant_created", "label": "Tenant account created"},
    {"key": "staff_added", "label": "At least one staff user added"},
    {"key": "policy_uploaded", "label": "First policy document uploaded"},
    {"key": "policyholder_added", "label": "At least one policyholder registered"},
    {"key": "query_tested", "label": "First policy query tested"},
    {"key": "widget_configured", "label": "Widget branding configured"},
    {"key": "go_live", "label": "Tenant marked as active"},
]


async def _compute_onboarding(db: AsyncSession, tenant: Tenant) -> OnboardingStatus:
    """Compute onboarding status by checking actual data + manual overrides."""
    tid = tenant.id
    stored = tenant.onboarding_status or {} if hasattr(tenant, "onboarding_status") else {}

    # Auto-detect steps from data
    staff_count = (await db.execute(select(func.count(StaffUser.id)).where(StaffUser.tenant_id == tid))).scalar() or 0
    doc_count = (await db.execute(select(func.count(Document.id)).where(Document.tenant_id == tid))).scalar() or 0
    ph_count = (await db.execute(select(func.count(Policyholder.id)).where(Policyholder.tenant_id == tid))).scalar() or 0

    from app.models.database import QueryLog
    query_count = (await db.execute(select(func.count(QueryLog.id)).where(QueryLog.tenant_id == tid))).scalar() or 0

    auto_checks = {
        "tenant_created": True,
        "staff_added": staff_count > 0,
        "policy_uploaded": doc_count > 0,
        "policyholder_added": ph_count > 0,
        "query_tested": query_count > 0,
        "widget_configured": bool(tenant.widget_config),
        "go_live": tenant.status.value == "active" if hasattr(tenant.status, "value") else tenant.status == "active",
    }

    steps = []
    for step_def in ONBOARDING_STEPS:
        key = step_def["key"]
        # Manual override takes precedence, then auto-detect
        manual = stored.get(key, {})
        completed = manual.get("completed", auto_checks.get(key, False))
        completed_at = manual.get("completed_at")

        steps.append({
            "key": key,
            "label": step_def["label"],
            "completed": completed,
            "completed_at": completed_at,
        })

    completed_count = sum(1 for s in steps if s["completed"])
    progress = (completed_count / len(steps)) * 100 if steps else 0

    return OnboardingStatus(
        tenant_id=str(tid),
        tenant_name=tenant.name,
        steps=steps,
        progress_pct=round(progress, 1),
        is_complete=completed_count == len(steps),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/{tenant_id}/onboarding", response_model=OnboardingStatus)
async def get_onboarding(
    tenant_id: str,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Get onboarding status for a tenant."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    return await _compute_onboarding(db, tenant)


@router.patch("/{tenant_id}/onboarding")
async def update_onboarding_step(
    tenant_id: str,
    body: OnboardingStepUpdate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Manually mark an onboarding step as completed or uncompleted."""
    valid_keys = {s["key"] for s in ONBOARDING_STEPS}
    if body.step_key not in valid_keys:
        raise HTTPException(status_code=400, detail=f"Invalid step: {body.step_key}")

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    stored = tenant.onboarding_status or {} if hasattr(tenant, "onboarding_status") else {}
    stored[body.step_key] = {
        "completed": body.completed,
        "completed_at": datetime.utcnow().isoformat() if body.completed else None,
    }
    tenant.onboarding_status = stored

    db.add(AuditLog(
        actor_id=admin["id"], actor_email=admin["email"],
        action="onboarding.step_update", resource_type="tenant",
        resource_id=tenant_id, details={"step": body.step_key, "completed": body.completed},
        ip_address=request.client.host if request and request.client else None,
    ))

    await db.commit()

    return await _compute_onboarding(db, tenant)

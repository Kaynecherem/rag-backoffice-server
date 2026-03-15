"""
Superadmin — Billing & Usage Tracking routes.

Plan management, usage monitoring per tenant. No payment integration yet.

UPDATED:
  - Plans are now editable via PATCH /plans/{plan_key}
  - Policy documents are UNLIMITED regardless of plan (document_limit
    only applies to communication documents)
  - batch_upload is always available for policy documents regardless of plan

Register in main.py:
    from app.api.routes.billing import router as billing_router
    app.include_router(billing_router, prefix="/api/v1/superadmin", tags=["superadmin-billing"])
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.database import (
    Tenant, StaffUser, Policyholder, Document, DocumentType, QueryLog, AuditLog,
)
from app.models.schemas import (
    PlanConfig, PlanConfigUpdate, TenantPlanAssign, TenantUsage, PlatformUsageSummary,
)
from app.api.dependencies import require_superadmin

logger = logging.getLogger("api.superadmin.billing")
router = APIRouter()

# ── Plan definitions (mutable in-memory, editable via API) ────────────
# In a future release these can be moved to a DB table.

PLANS: dict[str, PlanConfig] = {
    "trial": PlanConfig(
        name="Trial", query_limit_monthly=100, document_limit=20,
        staff_limit=2, policyholder_limit=0, features=["widget"],
    ),
    "starter": PlanConfig(
        name="Starter", query_limit_monthly=1000, document_limit=100,
        staff_limit=5, policyholder_limit=0, features=["widget", "batch_upload"],
    ),
    "professional": PlanConfig(
        name="Professional", query_limit_monthly=10000, document_limit=500,
        staff_limit=20, policyholder_limit=0, features=["widget", "batch_upload", "api_access"],
    ),
    "enterprise": PlanConfig(
        name="Enterprise", query_limit_monthly=0, document_limit=0,
        staff_limit=0, policyholder_limit=0, features=["widget", "batch_upload", "api_access", "custom_model"],
    ),
}


async def _log_action(db, actor, action, resource_type, resource_id=None, details=None, request=None):
    db.add(AuditLog(
        actor_id=actor["id"], actor_email=actor["email"], action=action,
        resource_type=resource_type, resource_id=str(resource_id) if resource_id else None,
        details=details, ip_address=request.client.host if request and request.client else None,
    ))


async def _get_tenant_usage(db: AsyncSession, tenant, period: str) -> TenantUsage:
    """Calculate usage for a tenant in a given month."""
    tid = tenant.id
    plan_key = getattr(tenant, "plan", None) or "trial"
    plan = PLANS.get(plan_key, PLANS["trial"])

    year, month = period.split("-")
    start = datetime(int(year), int(month), 1)
    if int(month) == 12:
        end = datetime(int(year) + 1, 1, 1)
    else:
        end = datetime(int(year), int(month) + 1, 1)

    queries_used = (await db.execute(
        select(func.count(QueryLog.id)).where(
            QueryLog.tenant_id == tid,
            QueryLog.queried_at >= start,
            QueryLog.queried_at < end,
        )
    )).scalar() or 0

    # Policy documents are UNLIMITED — only count communication docs against the limit
    total_doc_count = (await db.execute(select(func.count(Document.id)).where(Document.tenant_id == tid))).scalar() or 0
    policy_doc_count = (await db.execute(
        select(func.count(Document.id)).where(
            Document.tenant_id == tid,
            Document.document_type == DocumentType.POLICY,
        )
    )).scalar() or 0
    comm_doc_count = total_doc_count - policy_doc_count

    staff_count = (await db.execute(select(func.count(StaffUser.id)).where(StaffUser.tenant_id == tid))).scalar() or 0
    ph_count = (await db.execute(select(func.count(Policyholder.id)).where(Policyholder.tenant_id == tid))).scalar() or 0

    limit = plan.query_limit_monthly
    usage_pct = (queries_used / limit * 100) if limit > 0 else 0
    at_risk = limit > 0 and usage_pct >= 80

    return TenantUsage(
        tenant_id=str(tid), tenant_name=tenant.name, plan=plan_key, period=period,
        queries_used=queries_used, queries_limit=limit,
        documents_count=comm_doc_count, documents_limit=plan.document_limit,
        policy_documents_count=policy_doc_count, policy_documents_unlimited=True,
        staff_count=staff_count, staff_limit=plan.staff_limit,
        policyholders_count=ph_count, policyholders_limit=plan.policyholder_limit,
        usage_pct=round(usage_pct, 1), at_risk=at_risk,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/plans")
async def list_plans(admin: dict = Depends(require_superadmin)):
    """List all available plans."""
    return {key: plan.model_dump() for key, plan in PLANS.items()}


@router.patch("/plans/{plan_key}")
async def update_plan(
    plan_key: str,
    body: PlanConfigUpdate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Update limits and features for an existing plan."""
    if plan_key not in PLANS:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_key}' not found")

    current = PLANS[plan_key]
    changes = {}

    if body.name is not None:
        changes["name"] = body.name
    if body.query_limit_monthly is not None:
        changes["query_limit_monthly"] = body.query_limit_monthly
    if body.document_limit is not None:
        changes["document_limit"] = body.document_limit
    if body.staff_limit is not None:
        changes["staff_limit"] = body.staff_limit
    if body.policyholder_limit is not None:
        changes["policyholder_limit"] = body.policyholder_limit
    if body.features is not None:
        changes["features"] = body.features

    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Apply changes
    updated_data = current.model_dump()
    updated_data.update(changes)
    PLANS[plan_key] = PlanConfig(**updated_data)

    await _log_action(db, admin, "plan.update", "plan", plan_key, {
        "plan_key": plan_key,
        "changes": changes,
    }, request)
    await db.commit()

    logger.info(f"Plan '{plan_key}' updated by {admin['email']}: {changes}")
    return {plan_key: PLANS[plan_key].model_dump()}


@router.patch("/tenants/{tenant_id}/plan")
async def assign_plan(
    tenant_id: str,
    body: TenantPlanAssign,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Assign a plan to a tenant."""
    if body.plan not in PLANS:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {body.plan}. Available: {list(PLANS.keys())}")

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    old_plan = getattr(tenant, "plan", None) or "trial"
    tenant.plan = body.plan

    # Sync status with plan
    from app.models.database import TenantStatus
    if body.plan == "trial":
        tenant.status = TenantStatus.TRIAL
    elif tenant.status == TenantStatus.TRIAL:
        tenant.status = TenantStatus.ACTIVE

    await _log_action(db, admin, "tenant.plan_change", "tenant", tenant_id, {
        "tenant_name": tenant.name,
        "old_plan": old_plan,
        "new_plan": body.plan,
    }, request)
    await db.commit()

    return {"tenant_id": str(tenant.id), "plan": body.plan, "previous_plan": old_plan}


@router.get("/tenants/{tenant_id}/usage", response_model=TenantUsage)
async def get_tenant_usage(
    tenant_id: str,
    period: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Get current usage for a tenant."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if not period:
        period = datetime.utcnow().strftime("%Y-%m")

    return await _get_tenant_usage(db, tenant, period)


@router.get("/usage-summary", response_model=PlatformUsageSummary)
async def platform_usage_summary(
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Platform-wide usage overview."""
    period = datetime.utcnow().strftime("%Y-%m")
    year, month = period.split("-")
    start = datetime(int(year), int(month), 1)
    if int(month) == 12:
        end = datetime(int(year) + 1, 1, 1)
    else:
        end = datetime(int(year), int(month) + 1, 1)

    total_tenants = (await db.execute(select(func.count(Tenant.id)))).scalar() or 0
    active_tenants = (await db.execute(
        select(func.count(Tenant.id)).where(Tenant.status == "active")
    )).scalar() or 0
    total_queries = (await db.execute(
        select(func.count(QueryLog.id)).where(
            QueryLog.queried_at >= start, QueryLog.queried_at < end,
        )
    )).scalar() or 0
    total_documents = (await db.execute(select(func.count(Document.id)))).scalar() or 0
    total_staff = (await db.execute(select(func.count(StaffUser.id)))).scalar() or 0
    total_policyholders = (await db.execute(select(func.count(Policyholder.id)))).scalar() or 0

    # Find tenants at risk (>80% query usage)
    at_risk = []
    tenants_result = await db.execute(select(Tenant))
    for t in tenants_result.scalars().all():
        plan_key = getattr(t, "plan", None) or "trial"
        plan = PLANS.get(plan_key, PLANS["trial"])
        if plan.query_limit_monthly > 0:
            used = (await db.execute(
                select(func.count(QueryLog.id)).where(
                    QueryLog.tenant_id == t.id,
                    QueryLog.queried_at >= start, QueryLog.queried_at < end,
                )
            )).scalar() or 0
            pct = used / plan.query_limit_monthly * 100
            if pct >= 80:
                at_risk.append({
                    "tenant_id": str(t.id),
                    "tenant_name": t.name,
                    "plan": plan_key,
                    "usage_pct": round(pct, 1),
                })

    return PlatformUsageSummary(
        period=period,
        total_tenants=total_tenants,
        active_tenants=active_tenants,
        total_queries_this_month=total_queries,
        total_documents=total_documents,
        total_staff=total_staff,
        total_policyholders=total_policyholders,
        tenants_at_risk=at_risk,
    )
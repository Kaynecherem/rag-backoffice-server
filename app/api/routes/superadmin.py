"""
Superadmin API routes — platform-level tenant management.

Phase 1: Auth (login, setup, profile) + Tenant CRUD + Audit Logs + Platform Stats.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.vercel_domains import VercelDomainService

from app.db.session import get_db
from app.models.database import (
    Tenant, StaffUser, Policyholder, Document, QueryLog,
    TenantStatus, DocumentType,
    SuperAdmin, AuditLog,
)
from app.models.schemas import (
    SuperAdminLogin, SuperAdminLoginResponse, SuperAdminSetup, SuperAdminProfile,
    TenantCreate, TenantUpdate, TenantStatusUpdate,
    TenantListItem, TenantListResponse, TenantDetail,
    AuditLogItem, AuditLogResponse,
)
from app.core.security import hash_password, verify_password, create_superadmin_token
from app.api.dependencies import require_superadmin

logger = logging.getLogger("api.superadmin")
router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

async def _log_action(
    db: AsyncSession,
    actor: dict,
    action: str,
    resource_type: str,
    resource_id: str = None,
    details: dict = None,
    request: Request = None,
):
    """Write an audit log entry."""
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


async def _get_tenant_counts(db: AsyncSession, tenant_id) -> dict:
    """Get aggregate counts for a tenant."""
    staff_q = select(func.count(StaffUser.id)).where(StaffUser.tenant_id == tenant_id)
    holder_q = select(func.count(Policyholder.id)).where(Policyholder.tenant_id == tenant_id)
    doc_q = select(func.count(Document.id)).where(Document.tenant_id == tenant_id)
    query_q = select(func.count(QueryLog.id)).where(QueryLog.tenant_id == tenant_id)

    staff_count = (await db.execute(staff_q)).scalar() or 0
    holder_count = (await db.execute(holder_q)).scalar() or 0
    doc_count = (await db.execute(doc_q)).scalar() or 0
    query_count = (await db.execute(query_q)).scalar() or 0

    return {
        "staff_count": staff_count,
        "policyholder_count": holder_count,
        "document_count": doc_count,
        "query_count": query_count,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Auth Endpoints
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/auth/setup", response_model=SuperAdminLoginResponse)
async def initial_setup(
    body: SuperAdminSetup,
    db: AsyncSession = Depends(get_db),
):
    """
    One-time setup: create the first superadmin.
    Only works when no superadmins exist in the database.
    """
    existing = await db.execute(select(func.count(SuperAdmin.id)))
    count = existing.scalar()
    if count > 0:
        raise HTTPException(
            status_code=400,
            detail="Setup already completed. Use login or ask an existing superadmin to create your account.",
        )

    superadmin = SuperAdmin(
        email=body.email.lower().strip(),
        name=body.name.strip(),
        password_hash=hash_password(body.password),
        is_active=True,
    )
    db.add(superadmin)
    await db.commit()
    await db.refresh(superadmin)

    token = create_superadmin_token(str(superadmin.id), superadmin.email)

    logger.info(f"Initial superadmin created: {superadmin.email}")
    return SuperAdminLoginResponse(
        token=token,
        email=superadmin.email,
        name=superadmin.name,
    )


@router.post("/auth/login", response_model=SuperAdminLoginResponse)
async def login(
    body: SuperAdminLogin,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate a superadmin with email + password."""
    result = await db.execute(
        select(SuperAdmin).where(
            SuperAdmin.email == body.email.lower().strip(),
            SuperAdmin.is_active == True,
        )
    )
    superadmin = result.scalar_one_or_none()

    logger.info(f"User found: {superadmin is not None}")  # ADD THIS
    if superadmin:
        logger.info(f"Password check: {verify_password(body.password, superadmin.password_hash)}")  # ADD THIS

    if not superadmin or not verify_password(body.password, superadmin.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    superadmin.last_login_at = datetime.utcnow()
    await db.commit()

    token = create_superadmin_token(str(superadmin.id), superadmin.email)

    return SuperAdminLoginResponse(
        token=token,
        email=superadmin.email,
        name=superadmin.name,
    )


@router.get("/auth/me", response_model=SuperAdminProfile)
async def get_profile(
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Get the current superadmin's profile."""
    result = await db.execute(select(SuperAdmin).where(SuperAdmin.id == admin["id"]))
    superadmin = result.scalar_one_or_none()
    if not superadmin:
        raise HTTPException(status_code=404, detail="Superadmin not found")

    return SuperAdminProfile(
        id=str(superadmin.id),
        email=superadmin.email,
        name=superadmin.name,
        is_active=superadmin.is_active,
        last_login_at=superadmin.last_login_at,
        created_at=superadmin.created_at,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Tenant CRUD
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/tenants", response_model=TenantListResponse)
async def list_tenants(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, pattern=r"^(active|suspended|trial)$"),
    search: Optional[str] = None,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """List all tenants with counts, filterable by status and search."""
    filters = []
    if status:
        filters.append(Tenant.status == TenantStatus(status))
    if search:
        filters.append(Tenant.name.ilike(f"%{search}%"))

    count_q = select(func.count(Tenant.id))
    if filters:
        count_q = count_q.where(*filters)
    total = (await db.execute(count_q)).scalar() or 0

    query = (
        select(Tenant)
        .order_by(desc(Tenant.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if filters:
        query = query.where(*filters)

    result = await db.execute(query)
    tenants = result.scalars().all()

    items = []
    for t in tenants:
        counts = await _get_tenant_counts(db, t.id)
        items.append(TenantListItem(
            id=str(t.id),
            name=t.name,
            slug=t.slug,
            status=t.status.value if hasattr(t.status, "value") else str(t.status),
            created_at=t.created_at,
            **counts,
        ))

    return TenantListResponse(tenants=items, total=total, page=page, page_size=page_size)


@router.post("/tenants", response_model=TenantDetail, status_code=201)
async def create_tenant(
    body: TenantCreate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new tenant."""
    existing = await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Slug '{body.slug}' already in use")

    tenant = Tenant(
        name=body.name.strip(),
        slug=body.slug.strip().lower(),
        status=TenantStatus(body.status),
    )
    db.add(tenant)

    await _log_action(db, admin, "tenant.create", "tenant", details={
        "name": tenant.name, "slug": tenant.slug, "status": body.status,
    }, request=request)

    await db.commit()
    await db.refresh(tenant)

    # Auto-provision subdomain on Vercel
    vercel = VercelDomainService()
    domain_result = await vercel.add_domain(tenant.slug)

    # Include domain status in response
    # (don't fail tenant creation if Vercel fails — it can be retried)
    if domain_result.get("error"):
        logger.warning(f"Vercel domain provisioning failed for {tenant.slug}: {domain_result['error']}")

    return TenantDetail(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status.value if hasattr(tenant.status, "value") else str(tenant.status),
        widget_config=tenant.widget_config,
        created_at=tenant.created_at,
    )



@router.get("/tenants/{tenant_id}", response_model=TenantDetail)
async def get_tenant(
    tenant_id: str,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed info for a single tenant."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    counts = await _get_tenant_counts(db, tenant.id)

    policy_q = select(func.count(Document.id)).where(
        Document.tenant_id == tenant.id,
        Document.document_type == DocumentType.POLICY,
    )
    comm_q = select(func.count(Document.id)).where(
        Document.tenant_id == tenant.id,
        Document.document_type == DocumentType.COMMUNICATION,
    )
    policy_count = (await db.execute(policy_q)).scalar() or 0
    comm_count = (await db.execute(comm_q)).scalar() or 0

    recent_q = (
        select(QueryLog)
        .where(QueryLog.tenant_id == tenant.id)
        .order_by(desc(QueryLog.queried_at))
        .limit(5)
    )
    recent_result = await db.execute(recent_q)
    recent_logs = recent_result.scalars().all()
    recent_queries = [
        {
            "id": str(q.id),
            "question": q.question[:100],
            "user_type": q.user_type.value if hasattr(q.user_type, "value") else str(q.user_type),
            "queried_at": q.queried_at.isoformat(),
        }
        for q in recent_logs
    ]

    return TenantDetail(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status.value if hasattr(tenant.status, "value") else str(tenant.status),
        widget_config=tenant.widget_config,
        created_at=tenant.created_at,
        staff_count=counts["staff_count"],
        policyholder_count=counts["policyholder_count"],
        policy_count=policy_count,
        communication_count=comm_count,
        query_count=counts["query_count"],
        recent_queries=recent_queries,
    )


@router.put("/tenants/{tenant_id}", response_model=TenantDetail)
async def update_tenant(
    tenant_id: str,
    body: TenantUpdate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Update a tenant's name, slug, or widget config."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    changes = {}
    if body.name is not None:
        changes["name"] = {"from": tenant.name, "to": body.name}
        tenant.name = body.name.strip()
    if body.slug is not None:
        slug_check = await db.execute(
            select(Tenant).where(Tenant.slug == body.slug, Tenant.id != tenant_id)
        )
        if slug_check.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Slug '{body.slug}' already in use")
        changes["slug"] = {"from": tenant.slug, "to": body.slug}
        tenant.slug = body.slug.strip().lower()
    if body.widget_config is not None:
        changes["widget_config"] = "updated"
        tenant.widget_config = body.widget_config

    if changes:
        await _log_action(db, admin, "tenant.update", "tenant", tenant_id, changes, request)
        await db.commit()
        await db.refresh(tenant)

    counts = await _get_tenant_counts(db, tenant.id)

    return TenantDetail(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status.value if hasattr(tenant.status, "value") else str(tenant.status),
        widget_config=tenant.widget_config,
        created_at=tenant.created_at,
        **counts,
    )


@router.patch("/tenants/{tenant_id}/status")
async def update_tenant_status(
    tenant_id: str,
    body: TenantStatusUpdate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Activate, suspend, or set a tenant to trial status."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    old_status = tenant.status.value if hasattr(tenant.status, "value") else str(tenant.status)
    tenant.status = TenantStatus(body.status)

    await _log_action(db, admin, "tenant.status_change", "tenant", tenant_id, {
        "from": old_status, "to": body.status, "reason": body.reason,
    }, request)

    await db.commit()

    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "status": body.status,
        "previous_status": old_status,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Audit Logs
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/audit-logs", response_model=AuditLogResponse)
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """View audit trail of all superadmin actions."""
    filters = []
    if action:
        filters.append(AuditLog.action == action)
    if resource_type:
        filters.append(AuditLog.resource_type == resource_type)

    count_q = select(func.count(AuditLog.id))
    if filters:
        count_q = count_q.where(*filters)
    total = (await db.execute(count_q)).scalar() or 0

    query = (
        select(AuditLog)
        .order_by(desc(AuditLog.performed_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if filters:
        query = query.where(*filters)

    result = await db.execute(query)
    logs = result.scalars().all()

    return AuditLogResponse(
        logs=[
            AuditLogItem(
                id=str(log.id),
                actor_email=log.actor_email,
                action=log.action,
                resource_type=log.resource_type,
                resource_id=log.resource_id,
                details=log.details,
                performed_at=log.performed_at,
            )
            for log in logs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Platform Stats
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def platform_stats(
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """High-level platform stats for the superadmin dashboard."""
    tenant_count = (await db.execute(select(func.count(Tenant.id)))).scalar() or 0
    active_tenants = (await db.execute(
        select(func.count(Tenant.id)).where(Tenant.status == TenantStatus.ACTIVE)
    )).scalar() or 0
    total_staff = (await db.execute(select(func.count(StaffUser.id)))).scalar() or 0
    total_policyholders = (await db.execute(select(func.count(Policyholder.id)))).scalar() or 0
    total_documents = (await db.execute(select(func.count(Document.id)))).scalar() or 0
    total_queries = (await db.execute(select(func.count(QueryLog.id)))).scalar() or 0

    return {
        "tenants": {"total": tenant_count, "active": active_tenants},
        "staff": {"total": total_staff},
        "policyholders": {"total": total_policyholders},
        "documents": {"total": total_documents},
        "queries": {"total": total_queries},
    }

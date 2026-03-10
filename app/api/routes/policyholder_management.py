"""
Superadmin — Policyholder Management routes.

Manage policyholders across any tenant. Includes bulk import.

Register in main.py:
    from app.api.routes.policyholder_management import router as ph_mgmt_router
    app.include_router(ph_mgmt_router, prefix="/api/v1/superadmin/tenants", tags=["superadmin-policyholders"])
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.database import (
    Policyholder, Tenant, QueryLog, AuditLog,
)
from app.models.schemas import (
    PolicyholderCreate, PolicyholderUpdate, PolicyholderStatusUpdate,
    PolicyholderBulkImport,
    PolicyholderListItem, PolicyholderListResponse, BulkImportResult,
)
from app.api.dependencies import require_superadmin

logger = logging.getLogger("api.superadmin.policyholders")
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


async def _format_policyholder(db: AsyncSession, ph: Policyholder) -> PolicyholderListItem:
    # Count queries made by this policyholder
    q_count = await db.execute(
        select(func.count(QueryLog.id)).where(
            QueryLog.tenant_id == ph.tenant_id,
            QueryLog.policy_number == ph.policy_number,
            QueryLog.user_type == "policyholder",
        )
    )
    query_count = q_count.scalar() or 0

    return PolicyholderListItem(
        id=str(ph.id),
        tenant_id=str(ph.tenant_id),
        policy_number=ph.policy_number,
        last_name=ph.last_name,
        company_name=ph.company_name,
        is_active=ph.is_active,
        created_at=ph.created_at,
        query_count=query_count,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Policyholder CRUD (scoped to a tenant)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/{tenant_id}/policyholders", response_model=PolicyholderListResponse)
async def list_policyholders(
    tenant_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """List policyholders for a specific tenant."""
    await _get_tenant_or_404(db, tenant_id)

    filters = [Policyholder.tenant_id == tenant_id]
    if search:
        filters.append(
            (Policyholder.policy_number.ilike(f"%{search}%"))
            | (Policyholder.last_name.ilike(f"%{search}%"))
            | (Policyholder.company_name.ilike(f"%{search}%"))
        )
    if is_active is not None:
        filters.append(Policyholder.is_active == is_active)

    count_q = select(func.count(Policyholder.id)).where(*filters)
    total = (await db.execute(count_q)).scalar() or 0

    query = (
        select(Policyholder)
        .where(*filters)
        .order_by(desc(Policyholder.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    ph_list = result.scalars().all()

    items = []
    for ph in ph_list:
        items.append(await _format_policyholder(db, ph))

    return PolicyholderListResponse(
        policyholders=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{tenant_id}/policyholders", response_model=PolicyholderListItem, status_code=201)
async def create_policyholder(
    tenant_id: str,
    body: PolicyholderCreate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new policyholder for a tenant."""
    await _get_tenant_or_404(db, tenant_id)

    # Check for duplicate policy_number + last_name/company within tenant
    dup_filters = [
        Policyholder.tenant_id == tenant_id,
        func.lower(Policyholder.policy_number) == body.policy_number.strip().lower(),
    ]
    if body.last_name:
        dup_filters.append(func.lower(Policyholder.last_name) == body.last_name.strip().lower())
    if body.company_name:
        dup_filters.append(func.lower(Policyholder.company_name) == body.company_name.strip().lower())

    existing = await db.execute(select(Policyholder).where(*dup_filters))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Policyholder with policy '{body.policy_number}' already exists in this tenant",
        )

    ph = Policyholder(
        tenant_id=tenant_id,
        policy_number=body.policy_number.strip(),
        last_name=body.last_name.strip() if body.last_name else None,
        company_name=body.company_name.strip() if body.company_name else None,
        is_active=True,
    )
    db.add(ph)

    await _log_action(db, admin, "policyholder.create", "policyholder", details={
        "tenant_id": tenant_id,
        "policy_number": ph.policy_number,
    }, request=request)

    await db.commit()
    await db.refresh(ph)

    return await _format_policyholder(db, ph)


@router.get("/{tenant_id}/policyholders/{ph_id}", response_model=PolicyholderListItem)
async def get_policyholder(
    tenant_id: str,
    ph_id: str,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Get a single policyholder's details."""
    result = await db.execute(
        select(Policyholder).where(
            Policyholder.id == ph_id,
            Policyholder.tenant_id == tenant_id,
        )
    )
    ph = result.scalar_one_or_none()
    if not ph:
        raise HTTPException(status_code=404, detail="Policyholder not found")

    return await _format_policyholder(db, ph)


@router.put("/{tenant_id}/policyholders/{ph_id}", response_model=PolicyholderListItem)
async def update_policyholder(
    tenant_id: str,
    ph_id: str,
    body: PolicyholderUpdate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Update a policyholder's details."""
    result = await db.execute(
        select(Policyholder).where(
            Policyholder.id == ph_id,
            Policyholder.tenant_id == tenant_id,
        )
    )
    ph = result.scalar_one_or_none()
    if not ph:
        raise HTTPException(status_code=404, detail="Policyholder not found")

    changes = {}
    if body.policy_number is not None:
        changes["policy_number"] = {"from": ph.policy_number, "to": body.policy_number}
        ph.policy_number = body.policy_number.strip()
    if body.last_name is not None:
        changes["last_name"] = {"from": ph.last_name, "to": body.last_name}
        ph.last_name = body.last_name.strip() if body.last_name else None
    if body.company_name is not None:
        changes["company_name"] = {"from": ph.company_name, "to": body.company_name}
        ph.company_name = body.company_name.strip() if body.company_name else None

    if changes:
        await _log_action(db, admin, "policyholder.update", "policyholder", ph_id, {
            "tenant_id": tenant_id, **changes,
        }, request)
        await db.commit()
        await db.refresh(ph)

    return await _format_policyholder(db, ph)


@router.patch("/{tenant_id}/policyholders/{ph_id}/status")
async def update_policyholder_status(
    tenant_id: str,
    ph_id: str,
    body: PolicyholderStatusUpdate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Activate or deactivate a policyholder."""
    result = await db.execute(
        select(Policyholder).where(
            Policyholder.id == ph_id,
            Policyholder.tenant_id == tenant_id,
        )
    )
    ph = result.scalar_one_or_none()
    if not ph:
        raise HTTPException(status_code=404, detail="Policyholder not found")

    old_status = ph.is_active
    ph.is_active = body.is_active

    await _log_action(db, admin, "policyholder.status_change", "policyholder", ph_id, {
        "tenant_id": tenant_id,
        "policy_number": ph.policy_number,
        "from": old_status,
        "to": body.is_active,
    }, request)

    await db.commit()

    return {
        "id": str(ph.id),
        "policy_number": ph.policy_number,
        "is_active": ph.is_active,
        "previous_status": old_status,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Bulk Import
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/{tenant_id}/policyholders/bulk-import", response_model=BulkImportResult)
async def bulk_import_policyholders(
    tenant_id: str,
    body: PolicyholderBulkImport,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """
    Bulk import policyholders for a tenant.
    Skips duplicates (matching policy_number + last_name or company_name within tenant).
    """
    await _get_tenant_or_404(db, tenant_id)

    created = 0
    skipped = 0
    errors = []

    for idx, item in enumerate(body.policyholders):
        try:
            # Check for existing
            dup_filters = [
                Policyholder.tenant_id == tenant_id,
                func.lower(Policyholder.policy_number) == item.policy_number.strip().lower(),
            ]
            existing = await db.execute(select(Policyholder).where(*dup_filters))
            if existing.scalar_one_or_none():
                skipped += 1
                continue

            if not item.last_name and not item.company_name:
                errors.append(f"Row {idx + 1}: policy '{item.policy_number}' — must have last_name or company_name")
                continue

            ph = Policyholder(
                tenant_id=tenant_id,
                policy_number=item.policy_number.strip(),
                last_name=item.last_name.strip() if item.last_name else None,
                company_name=item.company_name.strip() if item.company_name else None,
                is_active=True,
            )
            db.add(ph)
            created += 1

        except Exception as e:
            errors.append(f"Row {idx + 1}: {str(e)}")

    if created > 0:
        await _log_action(db, admin, "policyholder.bulk_import", "policyholder", details={
            "tenant_id": tenant_id,
            "total_submitted": len(body.policyholders),
            "created": created,
            "skipped": skipped,
            "errors": len(errors),
        }, request=request)
        await db.commit()

    return BulkImportResult(created=created, skipped=skipped, errors=errors)

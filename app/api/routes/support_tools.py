"""
Superadmin — Support Tools routes.

Query lookup, policyholder verification debugging, and cache clearing.

Register in main.py:
    from app.api.routes.support_tools import router as support_router
    app.include_router(support_router, prefix="/api/v1/superadmin", tags=["superadmin-support"])
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.database import (
    Tenant, Policyholder, Document, QueryLog, AuditLog,
    DocumentStatus,
)
from app.models.schemas import (
    QueryLookupResult, QueryLookupResponse, VerificationDebugResult,
)
from app.api.dependencies import require_superadmin

logger = logging.getLogger("api.superadmin.support")
router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════
# Query Lookup — search across all tenants
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/support/query-lookup", response_model=QueryLookupResponse)
async def query_lookup(
    search: Optional[str] = None,
    tenant_id: Optional[str] = None,
    user_identifier: Optional[str] = None,
    policy_number: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """
    Search query logs across all tenants.
    Filter by text content, tenant, user, or policy number.
    """
    filters = []
    if search:
        filters.append(or_(
            QueryLog.question.ilike(f"%{search}%"),
            QueryLog.answer.ilike(f"%{search}%"),
        ))
    if tenant_id:
        filters.append(QueryLog.tenant_id == tenant_id)
    if user_identifier:
        filters.append(QueryLog.user_identifier.ilike(f"%{user_identifier}%"))
    if policy_number:
        filters.append(QueryLog.policy_number.ilike(f"%{policy_number}%"))

    count_q = select(func.count(QueryLog.id))
    if filters:
        count_q = count_q.where(*filters)
    total = (await db.execute(count_q)).scalar() or 0

    query = (
        select(QueryLog, Tenant.name.label("tenant_name"))
        .join(Tenant, Tenant.id == QueryLog.tenant_id)
        .order_by(desc(QueryLog.queried_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if filters:
        query = query.where(*filters)

    result = await db.execute(query)
    rows = result.all()

    queries = []
    for row in rows:
        q = row[0]  # QueryLog
        tenant_name = row[1]  # tenant_name
        queries.append(QueryLookupResult(
            id=str(q.id),
            tenant_name=tenant_name,
            user_type=q.user_type.value if hasattr(q.user_type, "value") else str(q.user_type),
            user_identifier=q.user_identifier,
            policy_number=q.policy_number,
            question=q.question,
            answer=q.answer,
            citations=q.citations,
            confidence=q.confidence,
            latency_ms=q.latency_ms,
            queried_at=q.queried_at,
        ))

    return QueryLookupResponse(queries=queries, total=total, page=page, page_size=page_size)


# ═══════════════════════════════════════════════════════════════════════════
# Verification Debug — check if a policyholder can authenticate
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/support/verify-debug", response_model=VerificationDebugResult)
async def verification_debug(
    tenant_id: str = Query(...),
    policy_number: str = Query(...),
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """
    Debug policyholder verification — checks if the policy exists,
    has a registered policyholder, has uploaded documents, and is indexed.
    """
    # Tenant
    t_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = t_result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Policyholder lookup (case-insensitive)
    ph_result = await db.execute(
        select(Policyholder).where(
            Policyholder.tenant_id == tenant_id,
            func.lower(Policyholder.policy_number) == policy_number.strip().lower(),
        )
    )
    policyholder = ph_result.scalar_one_or_none()

    # Documents for this policy
    doc_result = await db.execute(
        select(Document).where(
            Document.tenant_id == tenant_id,
            func.lower(Document.policy_number) == policy_number.strip().lower(),
        )
    )
    docs = doc_result.scalars().all()
    doc_count = len(docs)
    has_indexed = any(
        (d.status.value if hasattr(d.status, "value") else str(d.status)) == "indexed"
        for d in docs
    )

    return VerificationDebugResult(
        tenant_id=str(tenant.id),
        tenant_name=tenant.name,
        policy_number=policy_number.strip(),
        found=policyholder is not None,
        policyholder={
            "id": str(policyholder.id),
            "last_name": policyholder.last_name,
            "company_name": policyholder.company_name,
            "is_active": policyholder.is_active,
        } if policyholder else None,
        has_documents=doc_count > 0,
        document_count=doc_count,
        is_indexed=has_indexed,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Cache / Maintenance
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/support/clear-failed-docs")
async def clear_failed_documents(
    tenant_id: Optional[str] = Query(None),
    request: Request = None,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """
    Reset all FAILED documents back to UPLOADING status so they can be reprocessed.
    Optionally scoped to a specific tenant.
    """
    filters = [Document.status == DocumentStatus.FAILED]
    if tenant_id:
        filters.append(Document.tenant_id == tenant_id)

    result = await db.execute(select(Document).where(*filters))
    failed_docs = result.scalars().all()

    count = 0
    for doc in failed_docs:
        doc.status = DocumentStatus.UPLOADING
        count += 1

    if count > 0:
        db.add(AuditLog(
            actor_id=admin["id"], actor_email=admin["email"],
            action="support.clear_failed_docs", resource_type="document",
            details={"count": count, "tenant_id": tenant_id},
            ip_address=request.client.host if request and request.client else None,
        ))
        await db.commit()

    return {"reset_count": count, "tenant_id": tenant_id or "all"}


@router.get("/support/failed-docs-summary")
async def failed_docs_summary(
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Summary of failed documents across all tenants."""
    result = await db.execute(
        select(
            Tenant.name,
            Tenant.id,
            func.count(Document.id).label("count"),
        )
        .join(Document, Document.tenant_id == Tenant.id)
        .where(Document.status == DocumentStatus.FAILED)
        .group_by(Tenant.id, Tenant.name)
        .order_by(desc(func.count(Document.id)))
    )
    rows = result.all()

    total = sum(r.count for r in rows)
    return {
        "total_failed": total,
        "by_tenant": [
            {"tenant_id": str(r[1]), "tenant_name": r[0], "count": r[2]}
            for r in rows
        ],
    }

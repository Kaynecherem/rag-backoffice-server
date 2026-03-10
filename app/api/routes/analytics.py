"""
Superadmin — Analytics routes.

Platform-wide and per-tenant usage analytics.

Register in main.py:
    from app.api.routes.analytics import router as analytics_router
    app.include_router(analytics_router, prefix="/api/v1/superadmin", tags=["superadmin-analytics"])
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.database import (
    Tenant, Document, QueryLog, TenantStatus,
    DocumentType, DocumentStatus,
)
from app.models.schemas import PlatformAnalytics, TenantAnalytics
from app.api.dependencies import require_superadmin

logger = logging.getLogger("api.superadmin.analytics")
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────

def _days_ago(n: int) -> datetime:
    return datetime.utcnow() - timedelta(days=n)


# ═══════════════════════════════════════════════════════════════════════════
# Platform-wide Analytics
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/analytics", response_model=PlatformAnalytics)
async def platform_analytics(
    days: int = Query(30, ge=1, le=365),
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Platform-wide query analytics with time-series breakdown."""

    # Total queries
    total = (await db.execute(select(func.count(QueryLog.id)))).scalar() or 0

    # Queries in time windows
    q_30d = (await db.execute(
        select(func.count(QueryLog.id)).where(QueryLog.queried_at >= _days_ago(30))
    )).scalar() or 0
    q_7d = (await db.execute(
        select(func.count(QueryLog.id)).where(QueryLog.queried_at >= _days_ago(7))
    )).scalar() or 0
    q_today = (await db.execute(
        select(func.count(QueryLog.id)).where(QueryLog.queried_at >= _days_ago(1))
    )).scalar() or 0

    # Averages
    avg_conf = (await db.execute(
        select(func.avg(QueryLog.confidence)).where(
            QueryLog.confidence.isnot(None),
            QueryLog.queried_at >= _days_ago(days),
        )
    )).scalar()
    avg_lat = (await db.execute(
        select(func.avg(QueryLog.latency_ms)).where(
            QueryLog.latency_ms.isnot(None),
            QueryLog.queried_at >= _days_ago(days),
        )
    )).scalar()

    # Queries by day (for the chart)
    by_day_q = (
        select(
            cast(QueryLog.queried_at, Date).label("date"),
            func.count(QueryLog.id).label("count"),
        )
        .where(QueryLog.queried_at >= _days_ago(days))
        .group_by(cast(QueryLog.queried_at, Date))
        .order_by(cast(QueryLog.queried_at, Date))
    )
    by_day_result = await db.execute(by_day_q)
    queries_by_day = [
        {"date": row.date.isoformat(), "count": row.count}
        for row in by_day_result
    ]

    # Queries by user type
    by_user_q = (
        select(
            QueryLog.user_type,
            func.count(QueryLog.id).label("count"),
        )
        .where(QueryLog.queried_at >= _days_ago(days))
        .group_by(QueryLog.user_type)
    )
    by_user_result = await db.execute(by_user_q)
    queries_by_user_type = [
        {"user_type": row.user_type.value if hasattr(row.user_type, "value") else str(row.user_type), "count": row.count}
        for row in by_user_result
    ]

    # Queries by document type
    by_doctype_q = (
        select(
            QueryLog.document_type,
            func.count(QueryLog.id).label("count"),
        )
        .where(
            QueryLog.queried_at >= _days_ago(days),
            QueryLog.document_type.isnot(None),
        )
        .group_by(QueryLog.document_type)
    )
    by_doctype_result = await db.execute(by_doctype_q)
    queries_by_document_type = [
        {"document_type": row.document_type.value if hasattr(row.document_type, "value") else str(row.document_type), "count": row.count}
        for row in by_doctype_result
    ]

    # Top tenants by query count
    top_tenants_q = (
        select(
            QueryLog.tenant_id,
            Tenant.name,
            func.count(QueryLog.id).label("count"),
        )
        .join(Tenant, Tenant.id == QueryLog.tenant_id)
        .where(QueryLog.queried_at >= _days_ago(days))
        .group_by(QueryLog.tenant_id, Tenant.name)
        .order_by(desc(func.count(QueryLog.id)))
        .limit(10)
    )
    top_result = await db.execute(top_tenants_q)
    top_tenants = [
        {"tenant_id": str(row.tenant_id), "tenant_name": row.name, "count": row.count}
        for row in top_result
    ]

    return PlatformAnalytics(
        total_queries=total,
        queries_last_30d=q_30d,
        queries_last_7d=q_7d,
        queries_today=q_today,
        avg_confidence=round(avg_conf, 3) if avg_conf else None,
        avg_latency_ms=round(avg_lat, 1) if avg_lat else None,
        queries_by_day=queries_by_day,
        queries_by_user_type=queries_by_user_type,
        queries_by_document_type=queries_by_document_type,
        top_tenants=top_tenants,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Per-Tenant Analytics
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/tenants/{tenant_id}/analytics", response_model=TenantAnalytics)
async def tenant_analytics(
    tenant_id: str,
    days: int = Query(30, ge=1, le=365),
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Per-tenant query and document analytics."""

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    base = [QueryLog.tenant_id == tenant_id]

    total = (await db.execute(select(func.count(QueryLog.id)).where(*base))).scalar() or 0
    q_30d = (await db.execute(
        select(func.count(QueryLog.id)).where(*base, QueryLog.queried_at >= _days_ago(30))
    )).scalar() or 0
    q_7d = (await db.execute(
        select(func.count(QueryLog.id)).where(*base, QueryLog.queried_at >= _days_ago(7))
    )).scalar() or 0

    avg_conf = (await db.execute(
        select(func.avg(QueryLog.confidence)).where(
            *base, QueryLog.confidence.isnot(None), QueryLog.queried_at >= _days_ago(days),
        )
    )).scalar()
    avg_lat = (await db.execute(
        select(func.avg(QueryLog.latency_ms)).where(
            *base, QueryLog.latency_ms.isnot(None), QueryLog.queried_at >= _days_ago(days),
        )
    )).scalar()

    # Queries by day
    by_day_q = (
        select(
            cast(QueryLog.queried_at, Date).label("date"),
            func.count(QueryLog.id).label("count"),
        )
        .where(*base, QueryLog.queried_at >= _days_ago(days))
        .group_by(cast(QueryLog.queried_at, Date))
        .order_by(cast(QueryLog.queried_at, Date))
    )
    by_day_result = await db.execute(by_day_q)
    queries_by_day = [{"date": r.date.isoformat(), "count": r.count} for r in by_day_result]

    # By user type
    by_user_q = (
        select(QueryLog.user_type, func.count(QueryLog.id).label("count"))
        .where(*base, QueryLog.queried_at >= _days_ago(days))
        .group_by(QueryLog.user_type)
    )
    by_user_result = await db.execute(by_user_q)
    queries_by_user_type = [
        {"user_type": r.user_type.value if hasattr(r.user_type, "value") else str(r.user_type), "count": r.count}
        for r in by_user_result
    ]

    # Top policy numbers
    top_policies_q = (
        select(QueryLog.policy_number, func.count(QueryLog.id).label("count"))
        .where(*base, QueryLog.policy_number.isnot(None), QueryLog.queried_at >= _days_ago(days))
        .group_by(QueryLog.policy_number)
        .order_by(desc(func.count(QueryLog.id)))
        .limit(10)
    )
    top_pol_result = await db.execute(top_policies_q)
    top_policy_numbers = [{"policy_number": r.policy_number, "count": r.count} for r in top_pol_result]

    # Document stats
    doc_base = [Document.tenant_id == tenant_id]
    doc_total = (await db.execute(select(func.count(Document.id)).where(*doc_base))).scalar() or 0
    doc_policies = (await db.execute(
        select(func.count(Document.id)).where(*doc_base, Document.document_type == DocumentType.POLICY)
    )).scalar() or 0
    doc_comms = (await db.execute(
        select(func.count(Document.id)).where(*doc_base, Document.document_type == DocumentType.COMMUNICATION)
    )).scalar() or 0
    doc_indexed = (await db.execute(
        select(func.count(Document.id)).where(*doc_base, Document.status == DocumentStatus.INDEXED)
    )).scalar() or 0
    doc_failed = (await db.execute(
        select(func.count(Document.id)).where(*doc_base, Document.status == DocumentStatus.FAILED)
    )).scalar() or 0

    return TenantAnalytics(
        tenant_id=str(tenant.id),
        tenant_name=tenant.name,
        total_queries=total,
        queries_last_30d=q_30d,
        queries_last_7d=q_7d,
        avg_confidence=round(avg_conf, 3) if avg_conf else None,
        avg_latency_ms=round(avg_lat, 1) if avg_lat else None,
        queries_by_day=queries_by_day,
        queries_by_user_type=queries_by_user_type,
        top_policy_numbers=top_policy_numbers,
        document_stats={
            "total": doc_total,
            "policies": doc_policies,
            "communications": doc_comms,
            "indexed": doc_indexed,
            "failed": doc_failed,
        },
    )

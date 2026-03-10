"""
Superadmin — Document Management routes.

View, inspect, and delete documents across any tenant.

Register in main.py:
    from app.api.routes.document_management import router as doc_mgmt_router
    app.include_router(doc_mgmt_router, prefix="/api/v1/superadmin/tenants", tags=["superadmin-documents"])
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, desc, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.database import (
    Document, DocumentChunk, Tenant, AuditLog,
    DocumentType, DocumentStatus,
)
from app.models.schemas import (
    DocumentListItem, DocumentListResponse, DocumentDetail,
)
from app.api.dependencies import require_superadmin

logger = logging.getLogger("api.superadmin.documents")
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


def _format_doc(d: Document) -> DocumentListItem:
    return DocumentListItem(
        id=str(d.id),
        tenant_id=str(d.tenant_id),
        document_type=d.document_type.value if hasattr(d.document_type, "value") else str(d.document_type),
        status=d.status.value if hasattr(d.status, "value") else str(d.status),
        policy_number=d.policy_number,
        communication_type=d.communication_type,
        filename=d.filename,
        title=d.title,
        page_count=d.page_count,
        chunk_count=d.chunk_count,
        created_at=d.created_at,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Document Endpoints
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/{tenant_id}/documents", response_model=DocumentListResponse)
async def list_documents(
    tenant_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    document_type: Optional[str] = Query(None, pattern=r"^(policy|communication)$"),
    status: Optional[str] = Query(None, pattern=r"^(uploading|processing|indexed|failed)$"),
    search: Optional[str] = None,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """List documents for a tenant with filtering."""
    await _get_tenant_or_404(db, tenant_id)

    filters = [Document.tenant_id == tenant_id]
    if document_type:
        filters.append(Document.document_type == DocumentType(document_type))
    if status:
        filters.append(Document.status == DocumentStatus(status))
    if search:
        filters.append(
            (Document.filename.ilike(f"%{search}%"))
            | (Document.title.ilike(f"%{search}%"))
            | (Document.policy_number.ilike(f"%{search}%"))
        )

    count_q = select(func.count(Document.id)).where(*filters)
    total = (await db.execute(count_q)).scalar() or 0

    query = (
        select(Document)
        .where(*filters)
        .order_by(desc(Document.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    docs = result.scalars().all()

    return DocumentListResponse(
        documents=[_format_doc(d) for d in docs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{tenant_id}/documents/stats")
async def document_stats(
    tenant_id: str,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate document stats for a tenant."""
    await _get_tenant_or_404(db, tenant_id)

    base = [Document.tenant_id == tenant_id]

    total = (await db.execute(select(func.count(Document.id)).where(*base))).scalar() or 0
    policies = (await db.execute(
        select(func.count(Document.id)).where(*base, Document.document_type == DocumentType.POLICY)
    )).scalar() or 0
    communications = (await db.execute(
        select(func.count(Document.id)).where(*base, Document.document_type == DocumentType.COMMUNICATION)
    )).scalar() or 0
    indexed = (await db.execute(
        select(func.count(Document.id)).where(*base, Document.status == DocumentStatus.INDEXED)
    )).scalar() or 0
    processing = (await db.execute(
        select(func.count(Document.id)).where(*base, Document.status == DocumentStatus.PROCESSING)
    )).scalar() or 0
    failed = (await db.execute(
        select(func.count(Document.id)).where(*base, Document.status == DocumentStatus.FAILED)
    )).scalar() or 0

    total_pages = (await db.execute(
        select(func.sum(Document.page_count)).where(*base)
    )).scalar() or 0
    total_chunks = (await db.execute(
        select(func.sum(Document.chunk_count)).where(*base)
    )).scalar() or 0

    return {
        "total": total,
        "policies": policies,
        "communications": communications,
        "by_status": {
            "indexed": indexed,
            "processing": processing,
            "failed": failed,
            "uploading": total - indexed - processing - failed,
        },
        "total_pages": total_pages,
        "total_chunks": total_chunks,
    }


@router.get("/{tenant_id}/documents/{doc_id}", response_model=DocumentDetail)
async def get_document(
    tenant_id: str,
    doc_id: str,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Get document detail including chunk preview."""
    result = await db.execute(
        select(Document).where(
            Document.id == doc_id,
            Document.tenant_id == tenant_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Get first 10 chunks for preview
    chunk_q = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == doc.id)
        .order_by(DocumentChunk.chunk_index)
        .limit(10)
    )
    chunk_result = await db.execute(chunk_q)
    chunks = chunk_result.scalars().all()

    return DocumentDetail(
        id=str(doc.id),
        tenant_id=str(doc.tenant_id),
        document_type=doc.document_type.value if hasattr(doc.document_type, "value") else str(doc.document_type),
        status=doc.status.value if hasattr(doc.status, "value") else str(doc.status),
        policy_number=doc.policy_number,
        communication_type=doc.communication_type,
        filename=doc.filename,
        title=doc.title,
        s3_key=doc.s3_key,
        page_count=doc.page_count,
        chunk_count=doc.chunk_count,
        job_id=doc.job_id,
        created_at=doc.created_at,
        chunks=[
            {
                "index": c.chunk_index,
                "text": c.chunk_text[:300],
                "page": c.page_number,
                "section": c.section_title,
                "tokens": c.token_count,
            }
            for c in chunks
        ],
    )


@router.delete("/{tenant_id}/documents/{doc_id}")
async def delete_document(
    tenant_id: str,
    doc_id: str,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a document and its chunks from the database.

    NOTE: This does NOT delete from S3 or Pinecone — the main app's
    existing delete endpoints handle those side effects. This is a
    DB-level cleanup for the superadmin.
    """
    result = await db.execute(
        select(Document).where(
            Document.id == doc_id,
            Document.tenant_id == tenant_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc_info = {
        "filename": doc.filename,
        "document_type": doc.document_type.value if hasattr(doc.document_type, "value") else str(doc.document_type),
        "policy_number": doc.policy_number,
        "s3_key": doc.s3_key,
        "chunk_count": doc.chunk_count,
    }

    # Delete chunks first
    await db.execute(
        delete(DocumentChunk).where(DocumentChunk.document_id == doc.id)
    )

    # Delete document
    await db.delete(doc)

    await _log_action(db, admin, "document.delete", "document", doc_id, {
        "tenant_id": tenant_id,
        **doc_info,
    }, request)

    await db.commit()

    return {"deleted": True, "document": doc_info}

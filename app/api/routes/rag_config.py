"""
Superadmin — RAG Pipeline Management routes.

View and update chunking, embedding, retrieval, LLM, and prompt settings.
These are platform-level defaults stored in-memory for now (DB-backed later).

Register in main.py:
    from app.api.routes.rag_config import router as rag_config_router
    app.include_router(rag_config_router, prefix="/api/v1/superadmin", tags=["superadmin-rag"])
"""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.database import AuditLog
from app.models.schemas import RAGConfigResponse, RAGConfigUpdate
from app.api.dependencies import require_superadmin

logger = logging.getLogger("api.superadmin.rag")
router = APIRouter()

# ── Platform-wide RAG defaults (in-memory, persisted to DB later) ────────

_rag_config = {
    "chunking": {
        "strategy": "recursive",
        "chunk_size": 1000,
        "chunk_overlap": 200,
        "separator": "\n\n",
        "description": "Recursive text splitter with paragraph boundaries",
    },
    "embedding": {
        "model": "text-embedding-3-small",
        "provider": "openai",
        "dimensions": 1536,
        "batch_size": 100,
        "description": "OpenAI text-embedding-3-small (1536 dimensions)",
    },
    "retrieval": {
        "top_k": 8,
        "score_threshold": 0.3,
        "rerank_enabled": True,
        "rerank_top_n": 4,
        "namespace_isolation": True,
        "description": "Pinecone vector search with reranking",
    },
    "llm": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 2048,
        "temperature": 0.1,
        "description": "Claude Sonnet for RAG query answering",
    },
    "prompts": {
        "system_prompt": (
            "You are an insurance policy assistant. Answer questions based ONLY on the provided "
            "policy document excerpts. If the information is not in the provided context, say so clearly. "
            "Always cite the specific section or page when possible."
        ),
        "citation_instruction": "Include specific citations from the source material.",
        "no_answer_response": "I couldn't find information about that in the provided policy documents. Please contact your agent for assistance.",
        "confidence_thresholds": {
            "high": 0.8,
            "medium": 0.5,
            "low": 0.3,
        },
        "description": "Prompt templates and confidence thresholds",
    },
}


@router.get("/rag-config", response_model=RAGConfigResponse)
async def get_rag_config(admin: dict = Depends(require_superadmin)):
    """Get current RAG pipeline configuration."""
    return RAGConfigResponse(**_rag_config)


@router.put("/rag-config", response_model=RAGConfigResponse)
async def update_rag_config(
    body: RAGConfigUpdate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """
    Update RAG pipeline configuration.
    Only provided sections are updated (partial update).
    Changes take effect on next query — no restart needed.
    """
    updates = body.model_dump(exclude_none=True)

    if not updates:
        return RAGConfigResponse(**_rag_config)

    changed_sections = []
    for section, values in updates.items():
        if section in _rag_config and isinstance(values, dict):
            _rag_config[section] = {**_rag_config[section], **values}
            changed_sections.append(section)

    db.add(AuditLog(
        actor_id=admin["id"], actor_email=admin["email"],
        action="rag.config_update", resource_type="rag_config",
        details={"sections_updated": changed_sections},
        ip_address=request.client.host if request and request.client else None,
    ))
    await db.commit()

    logger.info(f"RAG config updated: {changed_sections} by {admin['email']}")
    return RAGConfigResponse(**_rag_config)


@router.post("/rag-config/reset", response_model=RAGConfigResponse)
async def reset_rag_config(
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Reset RAG configuration to defaults."""
    global _rag_config
    _rag_config = {
        "chunking": {"strategy": "recursive", "chunk_size": 1000, "chunk_overlap": 200, "separator": "\n\n", "description": "Recursive text splitter"},
        "embedding": {"model": "text-embedding-3-small", "provider": "openai", "dimensions": 1536, "batch_size": 100, "description": "OpenAI embeddings"},
        "retrieval": {"top_k": 8, "score_threshold": 0.3, "rerank_enabled": True, "rerank_top_n": 4, "namespace_isolation": True, "description": "Pinecone + reranking"},
        "llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "max_tokens": 2048, "temperature": 0.1, "description": "Claude Sonnet"},
        "prompts": {
            "system_prompt": "You are an insurance policy assistant. Answer questions based ONLY on the provided policy document excerpts.",
            "citation_instruction": "Include specific citations from the source material.",
            "no_answer_response": "I couldn't find information about that in the provided policy documents.",
            "confidence_thresholds": {"high": 0.8, "medium": 0.5, "low": 0.3},
            "description": "Default prompts",
        },
    }

    db.add(AuditLog(
        actor_id=admin["id"], actor_email=admin["email"],
        action="rag.config_reset", resource_type="rag_config",
        ip_address=request.client.host if request and request.client else None,
    ))
    await db.commit()

    return RAGConfigResponse(**_rag_config)

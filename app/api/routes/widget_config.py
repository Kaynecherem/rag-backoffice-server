"""
Superadmin — Widget Configuration routes.

Edit branding, colors, messages per tenant and generate embed code.

Register in main.py:
    from app.api.routes.widget_config import router as widget_config_router
    app.include_router(widget_config_router, prefix="/api/v1/superadmin/tenants", tags=["superadmin-widget"])
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.database import Tenant, AuditLog
from app.models.schemas import WidgetConfigUpdate, WidgetConfigResponse
from app.api.dependencies import require_superadmin

logger = logging.getLogger("api.superadmin.widget")
router = APIRouter()

# Default widget config template
DEFAULT_WIDGET_CONFIG = {
    "primary_color": "#2563eb",
    "header_text": "Policy Assistant",
    "welcome_message": "Hello! I can help you find information about your insurance policy.",
    "placeholder_text": "Ask about your policy...",
    "disclaimer_text": "This assistant provides information based on your policy documents. For binding decisions, please contact your agent directly.",
    "disclaimer_enabled": True,
    "logo_url": "",
    "position": "bottom-right",
}


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


def _generate_embed_code(tenant_id: str, cdn_base: str = "https://d28pes0iok9s89.cloudfront.net") -> str:
    """Generate the embeddable widget script tag."""
    return (
        f'<!-- Insurance RAG Widget -->\n'
        f'<script src="{cdn_base}/widget/insurance-rag-widget.js"></script>\n'
        f'<script>\n'
        f'  InsuranceRAGWidget.init({{\n'
        f'    tenantId: "{tenant_id}",\n'
        f'    apiUrl: "{cdn_base}"\n'
        f'  }});\n'
        f'</script>'
    )


# ═══════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/{tenant_id}/widget-config", response_model=WidgetConfigResponse)
async def get_widget_config(
    tenant_id: str,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Get widget configuration for a tenant."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Merge stored config with defaults
    stored = tenant.widget_config or {}
    config = {**DEFAULT_WIDGET_CONFIG, **stored}

    return WidgetConfigResponse(
        tenant_id=str(tenant.id),
        tenant_name=tenant.name,
        config=config,
        embed_code=_generate_embed_code(str(tenant.id)),
    )


@router.put("/{tenant_id}/widget-config", response_model=WidgetConfigResponse)
async def update_widget_config(
    tenant_id: str,
    body: WidgetConfigUpdate,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Update widget configuration for a tenant."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Get current config
    current = tenant.widget_config or {}

    # Apply updates (only non-None fields)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    new_config = {**current, **updates}
    tenant.widget_config = new_config

    await _log_action(db, admin, "widget.config_update", "tenant", tenant_id, {
        "fields_updated": list(updates.keys()),
    }, request)

    await db.commit()
    await db.refresh(tenant)

    # Merge with defaults for response
    config = {**DEFAULT_WIDGET_CONFIG, **new_config}

    return WidgetConfigResponse(
        tenant_id=str(tenant.id),
        tenant_name=tenant.name,
        config=config,
        embed_code=_generate_embed_code(str(tenant.id)),
    )


@router.post("/{tenant_id}/widget-config/reset", response_model=WidgetConfigResponse)
async def reset_widget_config(
    tenant_id: str,
    request: Request,
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Reset widget configuration to defaults."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.widget_config = None

    await _log_action(db, admin, "widget.config_reset", "tenant", tenant_id, request=request)

    await db.commit()

    return WidgetConfigResponse(
        tenant_id=str(tenant.id),
        tenant_name=tenant.name,
        config=DEFAULT_WIDGET_CONFIG,
        embed_code=_generate_embed_code(str(tenant.id)),
    )

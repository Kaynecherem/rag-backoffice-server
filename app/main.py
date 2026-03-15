"""
Superadmin Back Office — FastAPI Application Entry Point.

Connects to the SAME database as the main Insurance RAG app.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.session import engine

# Route imports
from app.api.routes.superadmin import router as superadmin_router
from app.api.routes.staff_management import router as staff_mgmt_router
from app.api.routes.policyholder_management import router as ph_mgmt_router
from app.api.routes.document_management import router as doc_mgmt_router
from app.api.routes.analytics import router as analytics_router
from app.api.routes.system_health import router as health_router
from app.api.routes.widget_config import router as widget_config_router
from app.api.routes.impersonation import router as impersonation_router
from app.api.routes.billing import router as billing_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.onboarding import router as onboarding_router
from app.api.routes.rag_config import router as rag_config_router
from app.api.routes.compliance import router as compliance_router
from app.api.routes.support_tools import router as support_router
from app.api.routes.superadmin_management import router as superadmin_mgmt_router

settings = get_settings()

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("superadmin")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Superadmin back office starting up")
    yield
    logger.info("Superadmin back office shutting down")
    await engine.dispose()


app = FastAPI(
    title="Patch — Superadmin API",
    description="Platform administration for Patch Policy Intelligence",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(superadmin_router, prefix="/api/v1/superadmin", tags=["superadmin"])

app.include_router(
    staff_mgmt_router,
    prefix="/api/v1/superadmin/tenants",
    tags=["superadmin-staff"],
)

app.include_router(
    ph_mgmt_router,
    prefix="/api/v1/superadmin/tenants",
    tags=["superadmin-policyholders"],
)

app.include_router(
    doc_mgmt_router,
    prefix="/api/v1/superadmin/tenants",
    tags=["superadmin-documents"],
)

app.include_router(
    analytics_router,
    prefix="/api/v1/superadmin",
    tags=["superadmin-analytics"],
)

app.include_router(
    health_router,
    prefix="/api/v1/superadmin",
    tags=["superadmin-system"],
)

app.include_router(
    widget_config_router,
    prefix="/api/v1/superadmin/tenants",
    tags=["superadmin-widget"],
)

app.include_router(
    impersonation_router,
    prefix="/api/v1/superadmin/tenants",
    tags=["superadmin-impersonation"],
)

app.include_router(
    billing_router,
    prefix="/api/v1/superadmin",
    tags=["superadmin-billing"],
)

app.include_router(
    notifications_router,
    prefix="/api/v1/superadmin",
    tags=["superadmin-notifications"],
)

app.include_router(
    onboarding_router,
    prefix="/api/v1/superadmin/tenants",
    tags=["superadmin-onboarding"],
)

app.include_router(
    rag_config_router,
    prefix="/api/v1/superadmin",
    tags=["superadmin-rag"],
)

app.include_router(
    compliance_router,
    prefix="/api/v1/superadmin/tenants",
    tags=["superadmin-compliance"],
)

app.include_router(
    support_router,
    prefix="/api/v1/superadmin",
    tags=["superadmin-support"],
)

# NEW: Superadmin account management
app.include_router(
    superadmin_mgmt_router,
    prefix="/api/v1/superadmin/admins",
    tags=["superadmin-management"],
)


# Health check
@app.get("/health")
async def health():
    return {"status": "ok", "service": "patch-superadmin"}
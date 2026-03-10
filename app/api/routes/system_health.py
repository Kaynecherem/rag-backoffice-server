"""
Superadmin — System Health routes.

Checks connectivity to PostgreSQL and reports system status.
Extensible for Redis, Pinecone, S3 checks when those services
are accessible from this backend.

Register in main.py:
    from app.api.routes.system_health import router as health_router
    app.include_router(health_router, prefix="/api/v1/superadmin", tags=["superadmin-system"])
"""

import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.schemas import ServiceStatus, SystemHealth
from app.api.dependencies import require_superadmin

logger = logging.getLogger("api.superadmin.health")
router = APIRouter()

# Track startup time
_startup_time = time.time()


async def _check_postgres(db: AsyncSession) -> ServiceStatus:
    """Check PostgreSQL connectivity and response time."""
    try:
        start = time.time()
        await db.execute(text("SELECT 1"))
        latency = (time.time() - start) * 1000

        # Get some DB stats
        result = await db.execute(text(
            "SELECT pg_database_size(current_database()), "
            "(SELECT count(*) FROM pg_stat_activity WHERE state = 'active')"
        ))
        row = result.fetchone()
        db_size_mb = round((row[0] or 0) / (1024 * 1024), 1)
        active_connections = row[1] or 0

        return ServiceStatus(
            name="PostgreSQL",
            status="ok" if latency < 500 else "degraded",
            latency_ms=round(latency, 1),
            details=f"Size: {db_size_mb}MB, Active connections: {active_connections}",
        )
    except Exception as e:
        logger.error(f"PostgreSQL health check failed: {e}")
        return ServiceStatus(
            name="PostgreSQL",
            status="down",
            details=str(e)[:200],
        )


async def _check_tables(db: AsyncSession) -> ServiceStatus:
    """Verify core tables exist and are accessible."""
    try:
        start = time.time()
        tables_to_check = [
            "tenants", "staff_users", "policyholders",
            "documents", "document_chunks", "query_logs",
            "super_admins", "audit_logs",
        ]
        missing = []
        for table in tables_to_check:
            try:
                await db.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
            except Exception:
                missing.append(table)

        latency = (time.time() - start) * 1000

        if missing:
            return ServiceStatus(
                name="Database Tables",
                status="degraded",
                latency_ms=round(latency, 1),
                details=f"Missing tables: {', '.join(missing)}",
            )

        return ServiceStatus(
            name="Database Tables",
            status="ok",
            latency_ms=round(latency, 1),
            details=f"All {len(tables_to_check)} core tables accessible",
        )
    except Exception as e:
        return ServiceStatus(
            name="Database Tables",
            status="down",
            details=str(e)[:200],
        )


async def _check_data_integrity(db: AsyncSession) -> ServiceStatus:
    """Quick data integrity check — counts across key tables."""
    try:
        start = time.time()
        result = await db.execute(text("""
            SELECT
                (SELECT count(*) FROM tenants) as tenants,
                (SELECT count(*) FROM staff_users) as staff,
                (SELECT count(*) FROM policyholders) as policyholders,
                (SELECT count(*) FROM documents) as documents,
                (SELECT count(*) FROM query_logs) as queries,
                (SELECT count(*) FROM documents WHERE status = 'failed') as failed_docs
        """))
        row = result.fetchone()
        latency = (time.time() - start) * 1000

        details = (
            f"Tenants: {row[0]}, Staff: {row[1]}, Policyholders: {row[2]}, "
            f"Documents: {row[3]}, Queries: {row[4]}, Failed docs: {row[5]}"
        )

        status = "ok"
        if row[5] and row[5] > 0 and row[3] and row[5] / row[3] > 0.1:
            status = "degraded"

        return ServiceStatus(
            name="Data Integrity",
            status=status,
            latency_ms=round(latency, 1),
            details=details,
        )
    except Exception as e:
        return ServiceStatus(
            name="Data Integrity",
            status="down",
            details=str(e)[:200],
        )


# ═══════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/system/health", response_model=SystemHealth)
async def system_health(
    admin: dict = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Comprehensive system health check."""
    services = []

    # Run all checks
    services.append(await _check_postgres(db))
    services.append(await _check_tables(db))
    services.append(await _check_data_integrity(db))

    # Determine overall status
    statuses = [s.status for s in services]
    if "down" in statuses:
        overall = "unhealthy"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    uptime = time.time() - _startup_time

    return SystemHealth(
        overall=overall,
        uptime_seconds=round(uptime, 1),
        services=services,
        checked_at=datetime.utcnow(),
    )

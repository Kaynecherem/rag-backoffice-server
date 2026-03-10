"""Shared API dependencies for authentication."""

from fastapi import Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.core.security import verify_superadmin_token
from app.models.database import SuperAdmin


async def require_superadmin(
    authorization: str = Header(..., description="Bearer token"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Require that the current user is an active superadmin.
    Checks both token validity AND that the superadmin exists and is active in DB.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization.split(" ", 1)[1]
    claims = verify_superadmin_token(token)

    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Verify superadmin still exists and is active
    result = await db.execute(
        select(SuperAdmin).where(
            SuperAdmin.id == claims["sub"],
            SuperAdmin.is_active == True,
        )
    )
    superadmin = result.scalar_one_or_none()
    if not superadmin:
        raise HTTPException(status_code=403, detail="Superadmin account inactive or not found")

    return {
        "id": str(superadmin.id),
        "email": superadmin.email,
        "name": superadmin.name,
        "role": "superadmin",
        "type": "superadmin_session",
    }

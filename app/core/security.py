"""Security utilities — JWT token management and password hashing."""

import bcrypt
from datetime import datetime, timedelta
from jose import jwt, JWTError

from app.config import get_settings

settings = get_settings()
ALGORITHM = "HS256"


# ── Password Hashing ──────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


# ── Superadmin Token ──────────────────────────────────────────────────────

def create_superadmin_token(superadmin_id: str, email: str, expires_hours: int = 12) -> str:
    """Create a JWT for an authenticated superadmin — no tenant_id."""
    payload = {
        "sub": superadmin_id,
        "email": email,
        "role": "superadmin",
        "type": "superadmin_session",
        "exp": datetime.utcnow() + timedelta(hours=expires_hours),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def verify_superadmin_token(token: str) -> dict | None:
    """Verify and decode a superadmin session token. Returns claims or None."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        if payload.get("type") != "superadmin_session":
            return None
        if payload.get("role") != "superadmin":
            return None
        return payload
    except JWTError:
        return None

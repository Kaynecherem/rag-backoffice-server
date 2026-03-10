"""
Seed script — creates a test superadmin user directly in the database.

Usage:
  # From within the Docker container:
  docker compose exec api python seed_superadmin.py

  # Or locally (with venv activated and DATABASE_URL_SYNC set):
  python seed_superadmin.py

  # With custom credentials:
  python seed_superadmin.py --email admin@test.com --password admin1234 --name "Test Admin"

The script is idempotent — if the email already exists, it resets the
password and reactivates the account instead of creating a duplicate.
"""

import argparse
import uuid
import sys
from datetime import datetime

import bcrypt
from sqlalchemy import create_engine, text

# ── Default test credentials ─────────────────────────────────────────────
DEFAULT_EMAIL = "superadmin@test.com"
DEFAULT_PASSWORD = "superadmin123"
DEFAULT_NAME = "Test Superadmin"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def get_db_url() -> str:
    """Try to load DATABASE_URL_SYNC from .env or environment."""
    import os

    # Try dotenv
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    url = os.environ.get("DATABASE_URL_SYNC")
    if not url:
        # Fall back to async URL and convert
        async_url = os.environ.get("DATABASE_URL", "")
        if async_url:
            url = async_url.replace("postgresql+asyncpg://", "postgresql://")

    if not url:
        url = "postgresql://postgres:postgres@localhost:5433/insurance_rag"
        print(f"  No DATABASE_URL found, using default: {url}")

    return url


def seed(email: str, password: str, name: str):
    db_url = get_db_url()
    engine = create_engine(db_url)

    password_hash = hash_password(password)

    with engine.connect() as conn:
        # Check if user already exists
        result = conn.execute(
            text("SELECT id, is_active FROM super_admins WHERE email = :email"),
            {"email": email.lower().strip()},
        )
        existing = result.fetchone()

        if existing:
            # Reset password and reactivate
            conn.execute(
                text("""
                     UPDATE super_admins
                     SET password_hash = :pw,
                         is_active     = true,
                         updated_at    = :now
                     WHERE email = :email
                     """),
                {"pw": password_hash, "now": datetime.utcnow(), "email": email.lower().strip()},
            )
            conn.commit()
            print(f"\n  ✓ Existing superadmin reset and reactivated")
        else:
            # Create new
            admin_id = str(uuid.uuid4())
            conn.execute(
                text("""
                     INSERT INTO super_admins (id, email, name, password_hash, is_active, created_at, updated_at)
                     VALUES (:id, :email, :name, :pw, true, :now, :now)
                     """),
                {
                    "id": admin_id,
                    "email": email.lower().strip(),
                    "name": name.strip(),
                    "pw": password_hash,
                    "now": datetime.utcnow(),
                },
            )
            conn.commit()
            print(f"\n  ✓ Superadmin created (id: {admin_id})")

        print(f"""
  ┌─────────────────────────────────────────┐
  │  Superadmin Test Credentials            │
  ├─────────────────────────────────────────┤
  │  Email:    {email:<28s} │
  │  Password: {password:<28s} │
  └─────────────────────────────────────────┘

  Login at: http://localhost:3001/superadmin/login
""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed a test superadmin user")
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--name", default=DEFAULT_NAME)
    args = parser.parse_args()

    try:
        seed(args.email, args.password, args.name)
    except Exception as e:
        print(f"\n  ✗ Failed: {e}", file=sys.stderr)
        sys.exit(1)
"""add notifications table and tenant plan columns

Revision ID: 002_phase5
Revises: 001_superadmin_init

Run: alembic upgrade head

NOTE: Update your alembic/env.py MANAGED_TABLES set to include "notifications":
    MANAGED_TABLES = {"super_admins", "audit_logs", "notifications"}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002_phase5"
down_revision: Union[str, None] = "001_superadmin_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Notifications table
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("notification_type", sa.String(50), nullable=False),
        sa.Column("target", sa.String(50), server_default="all"),
        sa.Column("target_tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_notifications_type_active", "notifications", ["notification_type", "is_active"])
    op.create_index("ix_notifications_target", "notifications", ["target", "target_tenant_id"])

    # Add plan and onboarding columns to tenants table
    op.add_column("tenants", sa.Column("plan", sa.String(100), server_default="trial"))
    op.add_column("tenants", sa.Column("onboarding_status", postgresql.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "onboarding_status")
    op.drop_column("tenants", "plan")
    op.drop_table("notifications")

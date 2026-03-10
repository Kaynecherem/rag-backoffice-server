"""
Database ORM models.

This file contains MIRRORS of the main app's tables that the superadmin
needs to query, plus the new superadmin-specific tables.

IMPORTANT: These model definitions MUST stay in sync with the main app's
app/models/database.py. If you add columns or change types in the main
app, update them here too.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Boolean, DateTime, Text, Integer, Float, JSON, Index,
    ForeignKey, UniqueConstraint,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ═══════════════════════════════════════════════════════════════════════════
# Enums (must match main app)
# ═══════════════════════════════════════════════════════════════════════════

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    STAFF = "staff"
    POLICYHOLDER = "policyholder"


class TenantStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"


class DocumentStatus(str, enum.Enum):
    UPLOADING = "uploading"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class DocumentType(str, enum.Enum):
    POLICY = "policy"
    COMMUNICATION = "communication"


# ═══════════════════════════════════════════════════════════════════════════
# Shared Models (mirrors of main app — READ + WRITE access)
# ═══════════════════════════════════════════════════════════════════════════

class Tenant(Base):
    """Insurance agency tenant."""
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    status = Column(SAEnum(TenantStatus), default=TenantStatus.TRIAL, nullable=False)
    widget_config = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    plan = Column(String(100), default="trial")
    onboarding_status = Column(JSON, nullable=True)

    # Relationships
    staff_users = relationship("StaffUser", back_populates="tenant")
    policyholders = relationship("Policyholder", back_populates="tenant")
    documents = relationship("Document", back_populates="tenant")
    query_logs = relationship("QueryLog", back_populates="tenant")


class StaffUser(Base):
    """Staff member within a tenant."""
    __tablename__ = "staff_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    auth0_user_id = Column(String(200), unique=True, nullable=False)
    email = Column(String(255), nullable=False)
    name = Column(String(255), nullable=True)
    role = Column(SAEnum(UserRole), default=UserRole.STAFF, nullable=False)
    is_active = Column(Boolean, default=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="staff_users")

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_staff_tenant_email"),
    )


class Policyholder(Base):
    """Policyholder linked to a specific policy within a tenant."""
    __tablename__ = "policyholders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    policy_number = Column(String(100), nullable=False)
    last_name = Column(String(255), nullable=True)
    company_name = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="policyholders")

    __table_args__ = (
        Index("ix_policyholders_lookup", "tenant_id", "policy_number", "last_name"),
        Index("ix_policyholders_company", "tenant_id", "policy_number", "company_name"),
    )


class Document(Base):
    """Uploaded document (policy or communication)."""
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    document_type = Column(SAEnum(DocumentType), nullable=False)
    status = Column(SAEnum(DocumentStatus), default=DocumentStatus.UPLOADING)
    policy_number = Column(String(100), nullable=True)
    communication_type = Column(String(100), nullable=True)
    filename = Column(String(500), nullable=True)
    title = Column(String(500), nullable=True)
    s3_key = Column(String(1000), nullable=True)
    page_count = Column(Integer, nullable=True)
    chunk_count = Column(Integer, nullable=True)
    job_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="documents")


class DocumentChunk(Base):
    """Individual chunk of a processed document."""
    __tablename__ = "document_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    page_number = Column(Integer, nullable=True)
    section_title = Column(String(500), nullable=True)
    token_count = Column(Integer, nullable=True)
    pinecone_id = Column(String(500), nullable=True)


class QueryLog(Base):
    """Audit log for every query made against the system."""
    __tablename__ = "query_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_type = Column(SAEnum(UserRole), nullable=False)
    user_identifier = Column(String(255), nullable=False)
    policy_number = Column(String(100), nullable=True)
    document_type = Column(SAEnum(DocumentType), nullable=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    citations = Column(JSON, nullable=True)
    confidence = Column(Float, nullable=True)
    retrieval_scores = Column(JSON, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    queried_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="query_logs")

    __table_args__ = (
        Index("ix_query_logs_tenant_date", "tenant_id", "queried_at"),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Superadmin-specific Models (NEW tables — only this app manages them)
# ═══════════════════════════════════════════════════════════════════════════

class SuperAdmin(Base):
    """Platform-level admin — not tied to any tenant."""
    __tablename__ = "super_admins"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(Base):
    """Every superadmin action logged for accountability."""
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id = Column(UUID(as_uuid=True), nullable=False)
    actor_email = Column(String(255), nullable=False)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(100), nullable=False)
    resource_id = Column(String(255), nullable=True)
    details = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
    performed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_audit_logs_actor", "actor_id", "performed_at"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_action_date", "action", "performed_at"),
    )

class Notification(Base):
    """Platform or tenant-scoped notifications/announcements."""
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    notification_type = Column(String(50), nullable=False)     # announcement, maintenance, alert, onboarding
    target = Column(String(50), default="all")                 # "all" or "tenant"
    target_tenant_id = Column(UUID(as_uuid=True), nullable=True)
    is_active = Column(Boolean, default=True)
    created_by = Column(String(255), nullable=False)           # superadmin email
    created_at = Column(DateTime, default=datetime.utcnow)
    scheduled_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_notifications_type_active", "notification_type", "is_active"),
        Index("ix_notifications_target", "target", "target_tenant_id"),
    )


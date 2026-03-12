"""Pydantic request/response schemas for superadmin endpoints."""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, EmailStr, Field


# ── Auth ───────────────────────────────────────────────────────────────────

class SuperAdminLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class SuperAdminLoginResponse(BaseModel):
    token: str
    email: str
    name: str


class SuperAdminSetup(BaseModel):
    """First-time setup — create the initial superadmin. Only works when none exist."""
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8)


class SuperAdminProfile(BaseModel):
    id: str
    email: str
    name: str
    is_active: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime


# ── Tenant CRUD ────────────────────────────────────────────────────────────

class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9\-]+$")
    status: str = Field(default="trial", pattern=r"^(active|suspended|trial)$")


class TenantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    slug: Optional[str] = Field(None, min_length=1, max_length=100, pattern=r"^[a-z0-9\-]+$")
    widget_config: Optional[dict] = None


class TenantStatusUpdate(BaseModel):
    status: str = Field(pattern=r"^(active|suspended|trial)$")
    reason: Optional[str] = None


class TenantListItem(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    created_at: datetime
    staff_count: int = 0
    policyholder_count: int = 0
    document_count: int = 0
    query_count: int = 0


class TenantDetail(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    widget_config: Optional[dict] = None
    created_at: datetime
    staff_count: int = 0
    policyholder_count: int = 0
    policy_count: int = 0
    communication_count: int = 0
    query_count: int = 0
    recent_queries: list = []


class TenantListResponse(BaseModel):
    tenants: list[TenantListItem]
    total: int
    page: int
    page_size: int


# ── Audit Log ──────────────────────────────────────────────────────────────

class AuditLogItem(BaseModel):
    id: str
    actor_email: str
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    details: Optional[dict] = None
    performed_at: datetime


class AuditLogResponse(BaseModel):
    logs: list[AuditLogItem]
    total: int
    page: int
    page_size: int

# ═══════════════════════════════════════════════════════════════════════════
# Staff User Management
# ═══════════════════════════════════════════════════════════════════════════

class StaffCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    role: str = Field(default="staff", pattern=r"^(admin|staff)$")
    auth0_user_id: Optional[str] = Field(None, max_length=200)


class StaffUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    role: Optional[str] = Field(None, pattern=r"^(admin|staff)$")
    email: Optional[str] = Field(None, max_length=255)


class StaffStatusUpdate(BaseModel):
    is_active: bool


class StaffListItem(BaseModel):
    id: str
    tenant_id: str
    email: str
    name: Optional[str] = None
    role: str
    is_active: bool
    auth0_user_id: Optional[str] = None
    last_login_at: Optional[datetime] = None
    created_at: datetime


class StaffListResponse(BaseModel):
    staff: list[StaffListItem]
    total: int
    page: int
    page_size: int


# ═══════════════════════════════════════════════════════════════════════════
# Policyholder Management
# ═══════════════════════════════════════════════════════════════════════════

class PolicyholderCreate(BaseModel):
    policy_number: str = Field(min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, max_length=255)
    company_name: Optional[str] = Field(None, max_length=500)


class PolicyholderUpdate(BaseModel):
    policy_number: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, max_length=255)
    company_name: Optional[str] = Field(None, max_length=500)


class PolicyholderStatusUpdate(BaseModel):
    is_active: bool


class PolicyholderBulkImportItem(BaseModel):
    policy_number: str = Field(min_length=1, max_length=100)
    last_name: Optional[str] = None
    company_name: Optional[str] = None


class PolicyholderBulkImport(BaseModel):
    policyholders: list[PolicyholderBulkImportItem] = Field(min_length=1, max_length=500)


class PolicyholderListItem(BaseModel):
    id: str
    tenant_id: str
    policy_number: str
    last_name: Optional[str] = None
    company_name: Optional[str] = None
    is_active: bool
    created_at: datetime
    query_count: int = 0


class PolicyholderListResponse(BaseModel):
    policyholders: list[PolicyholderListItem]
    total: int
    page: int
    page_size: int


class BulkImportResult(BaseModel):
    created: int
    skipped: int
    errors: list[str]

# ═══════════════════════════════════════════════════════════════════════════
# Document Management
# ═══════════════════════════════════════════════════════════════════════════

class DocumentListItem(BaseModel):
    id: str
    tenant_id: str
    document_type: str
    status: str
    policy_number: Optional[str] = None
    communication_type: Optional[str] = None
    filename: Optional[str] = None
    title: Optional[str] = None
    page_count: Optional[int] = None
    chunk_count: Optional[int] = None
    created_at: datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentListItem]
    total: int
    page: int
    page_size: int


class DocumentDetail(BaseModel):
    id: str
    tenant_id: str
    document_type: str
    status: str
    policy_number: Optional[str] = None
    communication_type: Optional[str] = None
    filename: Optional[str] = None
    title: Optional[str] = None
    s3_key: Optional[str] = None
    page_count: Optional[int] = None
    chunk_count: Optional[int] = None
    job_id: Optional[str] = None
    created_at: datetime
    chunks: list[dict] = []


# ═══════════════════════════════════════════════════════════════════════════
# Analytics
# ═══════════════════════════════════════════════════════════════════════════

class PlatformAnalytics(BaseModel):
    total_queries: int
    queries_last_30d: int
    queries_last_7d: int
    queries_today: int
    avg_confidence: Optional[float] = None
    avg_latency_ms: Optional[float] = None
    queries_by_day: list[dict] = []
    queries_by_user_type: list[dict] = []
    queries_by_document_type: list[dict] = []
    top_tenants: list[dict] = []


class TenantAnalytics(BaseModel):
    tenant_id: str
    tenant_name: str
    total_queries: int
    queries_last_30d: int
    queries_last_7d: int
    avg_confidence: Optional[float] = None
    avg_latency_ms: Optional[float] = None
    queries_by_day: list[dict] = []
    queries_by_user_type: list[dict] = []
    top_policy_numbers: list[dict] = []
    document_stats: dict = {}

# ═══════════════════════════════════════════════════════════════════════════
# System Health
# ═══════════════════════════════════════════════════════════════════════════

class ServiceStatus(BaseModel):
    name: str
    status: str           # "ok", "degraded", "down"
    latency_ms: Optional[float] = None
    details: Optional[str] = None


class SystemHealth(BaseModel):
    overall: str          # "healthy", "degraded", "unhealthy"
    uptime_seconds: Optional[float] = None
    services: list[ServiceStatus] = []
    checked_at: datetime


# ═══════════════════════════════════════════════════════════════════════════
# Widget Configuration
# ═══════════════════════════════════════════════════════════════════════════

class WidgetConfigUpdate(BaseModel):
    primary_color: Optional[str] = Field(None, max_length=7, pattern=r"^#[0-9a-fA-F]{6}$")
    header_text: Optional[str] = Field(None, max_length=255)
    welcome_message: Optional[str] = Field(None, max_length=1000)
    placeholder_text: Optional[str] = Field(None, max_length=255)
    disclaimer_text: Optional[str] = Field(None, max_length=2000)
    disclaimer_enabled: Optional[bool] = None
    logo_url: Optional[str] = Field(None, max_length=500)
    position: Optional[str] = Field(None, pattern=r"^(bottom-right|bottom-left)$")


class WidgetConfigResponse(BaseModel):
    tenant_id: str
    tenant_name: str
    config: dict
    embed_code: str


# ═══════════════════════════════════════════════════════════════════════════
# Impersonation
# ═══════════════════════════════════════════════════════════════════════════

class ImpersonateStaffRequest(BaseModel):
    staff_id: Optional[str] = None    # specific staff, or first active admin if omitted
    role: str = Field(default="admin", pattern=r"^(admin|staff)$")


class ImpersonatePolicyholderRequest(BaseModel):
    policy_number: str = Field(min_length=1)


class ImpersonationToken(BaseModel):
    token: str
    impersonating: str      # "staff" or "policyholder"
    tenant_id: str
    tenant_name: str
    user_identifier: str    # email or policy_number
    role: str
    expires_in_hours: int
    notice: str             # reminder that actions are logged

# ═══════════════════════════════════════════════════════════════════════════
# Billing & Subscription (usage tracking, no payments)
# ═══════════════════════════════════════════════════════════════════════════

class PlanConfig(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    query_limit_monthly: int = Field(ge=0)           # 0 = unlimited
    document_limit: int = Field(ge=0)                 # 0 = unlimited
    staff_limit: int = Field(ge=0)
    policyholder_limit: int = Field(ge=0)
    features: list[str] = []                          # e.g. ["widget", "batch_upload", "api_access"]


class TenantPlanAssign(BaseModel):
    plan: str = Field(min_length=1, max_length=100)


class TenantUsage(BaseModel):
    tenant_id: str
    tenant_name: str
    plan: str
    period: str                                        # "2026-03"
    queries_used: int
    queries_limit: int
    documents_count: int
    documents_limit: int
    staff_count: int
    staff_limit: int
    policyholders_count: int
    policyholders_limit: int
    usage_pct: float                                   # queries_used / queries_limit * 100
    at_risk: bool                                      # approaching or over limit


class PlatformUsageSummary(BaseModel):
    total_queries_this_month: int
    total_documents: int
    tenants_at_risk: list[dict] = []
    usage_by_tenant: list[dict] = []


# ═══════════════════════════════════════════════════════════════════════════
# Notifications
# ═══════════════════════════════════════════════════════════════════════════

class NotificationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1, max_length=5000)
    notification_type: str = Field(pattern=r"^(announcement|maintenance|alert|onboarding)$")
    target: str = Field(default="all", pattern=r"^(all|tenant)$")
    target_tenant_id: Optional[str] = None
    scheduled_at: Optional[datetime] = None


class NotificationItem(BaseModel):
    id: str
    title: str
    message: str
    notification_type: str
    target: str
    target_tenant_id: Optional[str] = None
    is_active: bool
    created_by: str
    created_at: datetime
    scheduled_at: Optional[datetime] = None


class NotificationListResponse(BaseModel):
    notifications: list[NotificationItem]
    total: int
    page: int
    page_size: int


# ═══════════════════════════════════════════════════════════════════════════
# Onboarding Workflow
# ═══════════════════════════════════════════════════════════════════════════

class OnboardingStatus(BaseModel):
    tenant_id: str
    tenant_name: str
    steps: list[dict]                                  # [{key, label, completed, completed_at}]
    progress_pct: float
    is_complete: bool


class OnboardingStepUpdate(BaseModel):
    step_key: str = Field(min_length=1)
    completed: bool


# ═══════════════════════════════════════════════════════════════════════════
# RAG Pipeline Management
# ═══════════════════════════════════════════════════════════════════════════

class RAGConfigResponse(BaseModel):
    chunking: dict
    embedding: dict
    retrieval: dict
    llm: dict
    prompts: dict


class RAGConfigUpdate(BaseModel):
    chunking: Optional[dict] = None
    embedding: Optional[dict] = None
    retrieval: Optional[dict] = None
    llm: Optional[dict] = None
    prompts: Optional[dict] = None


# ═══════════════════════════════════════════════════════════════════════════
# Disclaimer & Compliance
# ═══════════════════════════════════════════════════════════════════════════

class DisclaimerConfig(BaseModel):
    tenant_id: str
    tenant_name: str
    disclaimer_text: str
    disclaimer_enabled: bool
    acceptance_count: int
    last_updated: Optional[datetime] = None


class DisclaimerUpdate(BaseModel):
    disclaimer_text: Optional[str] = Field(None, max_length=5000)
    disclaimer_enabled: Optional[bool] = None


# ═══════════════════════════════════════════════════════════════════════════
# Support Tools
# ═══════════════════════════════════════════════════════════════════════════

class QueryLookupResult(BaseModel):
    id: str
    tenant_name: str
    user_type: str
    user_identifier: str
    policy_number: Optional[str]
    question: str
    answer: Optional[str]
    citations: Optional[list] = None
    confidence: Optional[float] = None
    latency_ms: Optional[int] = None
    queried_at: datetime


class QueryLookupResponse(BaseModel):
    queries: list[QueryLookupResult]
    total: int
    page: int
    page_size: int


class VerificationDebugResult(BaseModel):
    tenant_id: str
    tenant_name: str
    policy_number: str
    found: bool
    policyholder: Optional[dict] = None
    has_documents: bool
    document_count: int
    is_indexed: bool
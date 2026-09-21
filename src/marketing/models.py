"""Domain models for the marketing control plane."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class ConnectorStatus(str, Enum):
    NOT_CONFIGURED = "not_configured"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    ERROR = "error"


class MarketingAction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    agent: str
    action_type: str
    provider: str
    title: str
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    risk: RiskLevel
    requires_approval: bool = True
    reversible: bool = False
    status: ActionStatus = ActionStatus.DRAFT
    created_at: datetime = Field(default_factory=utc_now)
    approved_by: str | None = None
    approved_at: datetime | None = None


class ApprovalDecision(BaseModel):
    approved: bool
    actor: str = Field(min_length=2, max_length=120)
    reason: str = Field(default="", max_length=1000)


class ContentSource(BaseModel):
    source_type: str
    text: str = ""
    product_url: str | None = None
    media_urls: list[str] = Field(default_factory=list)
    language: str = "he"


class ContentDraft(BaseModel):
    channel: str
    format: str
    headline: str
    body: str
    call_to_action: str
    utm_url: str | None = None
    variant: str = "A"
    claims_used: list[str] = Field(default_factory=list)


class PerformanceMetric(BaseModel):
    content_id: str
    channel: str
    impressions: int = 0
    views: int = 0
    engagements: int = 0
    clicks: int = 0
    conversions: int = 0
    revenue: float = 0
    spend: float = 0


class ConnectorHealth(BaseModel):
    provider: str
    status: ConnectorStatus
    mode: str = "read_only"
    last_sync_at: datetime | None = None
    permissions: list[str] = Field(default_factory=list)
    error: str | None = None

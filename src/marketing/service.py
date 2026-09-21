"""Application service composing agents, policies, approval state and MCP."""
from __future__ import annotations

from src.marketing.agents import (
    AgentCoordinator,
    AnalyticsAgent,
    CampaignManagerAgent,
    ContentCreatorAgent,
    EngagementAgent,
)
from src.marketing.content_engine import ContentRepurposer
from src.marketing.mcp_gateway import MetaMCPGateway
from src.marketing.models import ActionStatus, ApprovalDecision, MarketingAction, utc_now
from src.marketing.performance import PerformanceAnalyzer
from src.marketing.policy import MarketingPolicy
from src.marketing.store import MarketingStore


class MarketingPlatform:
    def __init__(self, db_path: str = "data/marketing.db"):
        self.store = MarketingStore(db_path)
        self.policy = MarketingPolicy(monthly_budget_ils=5000, max_budget_increase_pct=10)
        self.coordinator = AgentCoordinator()
        self.coordinator.register(ContentCreatorAgent(ContentRepurposer(
            brand_name="הגלריה לעיצוב הבית – סמדר בטיטו",
            approved_claims=["אמינות, איכות ושירות מעל הכול"],
        )))
        self.coordinator.register(AnalyticsAgent(PerformanceAnalyzer()))
        self.coordinator.register(CampaignManagerAgent(self.policy))
        self.coordinator.register(EngagementAgent())
        self.meta = MetaMCPGateway(self.policy, self.store)

    def propose(self, provider: str, operation: str, title: str, payload: dict,
                evidence: dict, confidence: float) -> MarketingAction:
        decision = self.policy.evaluate(provider, operation, payload)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        if not self.policy.require_system_test_name(provider, payload.get("name", "")):
            raise ValueError("שם קמפיין Google חדש חייב להתחיל ב-SYSTEM_TEST_")
        action = MarketingAction(
            agent="orchestrator",
            action_type=operation,
            provider=provider,
            title=title,
            payload=payload,
            evidence={**evidence, "policy_reason": decision.reason},
            confidence=confidence,
            risk=decision.risk,
            requires_approval=decision.requires_approval,
            status=ActionStatus.PENDING_APPROVAL if decision.requires_approval else ActionStatus.DRAFT,
        )
        return self.store.save_action(action)

    def decide(self, action_id: str, decision: ApprovalDecision) -> MarketingAction:
        action = self.store.get_action(action_id)
        if action is None:
            raise KeyError(action_id)
        if action.status != ActionStatus.PENDING_APPROVAL:
            raise ValueError("ניתן לאשר רק פעולה שממתינה לאישור")
        action.status = ActionStatus.APPROVED if decision.approved else ActionStatus.REJECTED
        action.approved_by = decision.actor
        action.approved_at = utc_now()
        self.store.save_action(action, actor=decision.actor)
        self.store.record(action.id, "approved" if decision.approved else "rejected",
                          decision.actor, {"reason": decision.reason})
        return action

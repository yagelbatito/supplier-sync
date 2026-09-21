"""Registry-based multi-agent coordinator inspired by the referenced projects."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from src.marketing.content_engine import ContentRepurposer
from src.marketing.models import ContentSource, PerformanceMetric
from src.marketing.performance import PerformanceAnalyzer
from src.marketing.policy import MarketingPolicy


class Agent(Protocol):
    name: str

    def run(self, task: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class ContentCreatorAgent:
    engine: ContentRepurposer
    name: str = "content_creator"

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        source = ContentSource.model_validate(task["source"])
        drafts = self.engine.create_drafts(source, task.get("campaign", "content_repurpose"))
        return {"drafts": [item.model_dump(mode="json") for item in drafts], "count": len(drafts)}


@dataclass
class AnalyticsAgent:
    analyzer: PerformanceAnalyzer
    name: str = "analytics"

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        metrics = [PerformanceMetric.model_validate(item) for item in task.get("metrics", [])]
        ranked = self.analyzer.rank(metrics)
        return {"ranking": [item.__dict__ for item in ranked]}


@dataclass
class CampaignManagerAgent:
    policy: MarketingPolicy
    name: str = "ad_campaign_manager"

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        provider = task.get("provider", "meta")
        operation = task.get("operation", "create_campaign")
        payload = task.get("payload", {})
        decision = self.policy.evaluate(provider, operation, payload)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        if not self.policy.require_system_test_name(provider, payload.get("name", "")):
            raise ValueError("שם קמפיין Google חדש חייב להתחיל ב-SYSTEM_TEST_")
        return {
            "draft_only": True,
            "requires_approval": decision.requires_approval,
            "risk": decision.risk.value,
            "policy_reason": decision.reason,
            "provider": provider,
            "operation": operation,
            "payload": payload,
        }


@dataclass
class EngagementAgent:
    name: str = "engagement"

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        incoming = " ".join(str(task.get("message", "")).split()).strip()
        if not incoming:
            raise ValueError("נדרשת הודעת מקור")
        return {
            "draft_only": True,
            "requires_approval": True,
            "language": task.get("language", "he"),
            "reply": f"תודה שפנית אלינו. קיבלנו את ההודעה: {incoming[:240]}",
            "reason": "אין לשלוח הודעה שיווקית או תגובה אוטומטית ללא בדיקה והרשאה",
        }


class AgentCoordinator:
    def __init__(self):
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        if agent.name in self._agents:
            raise ValueError(f"Agent already registered: {agent.name}")
        self._agents[agent.name] = agent

    def run(self, agent_name: str, task: dict[str, Any]) -> dict[str, Any]:
        if agent_name not in self._agents:
            raise KeyError(f"Unknown agent: {agent_name}")
        return self._agents[agent_name].run(task)

    @property
    def registry(self) -> list[str]:
        return sorted(self._agents)

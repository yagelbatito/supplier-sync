"""Policy gateway for the optional 135-tool Meta Ads MCP server."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.marketing.models import ActionStatus, MarketingAction
from src.marketing.policy import MarketingPolicy
from src.marketing.store import MarketingStore


class MetaMCPGateway:
    """Admits read tools and converts mutation tools into approval records.

    ``transport`` is intentionally injected. Production can provide a stdio or
    HTTP MCP client; tests and local development remain credential-free.
    """

    def __init__(self, policy: MarketingPolicy, store: MarketingStore,
                 transport: Callable[[str, dict], Any] | None = None):
        self.policy = policy
        self.store = store
        self.transport = transport

    def call(self, tool_name: str, arguments: dict | None = None) -> Any:
        arguments = arguments or {}
        decision = self.policy.evaluate("meta", tool_name, arguments)
        if not decision.allowed:
            raise PermissionError(decision.reason)

        if decision.requires_approval:
            action = MarketingAction(
                agent="ad_campaign_manager",
                action_type=tool_name,
                provider="meta",
                title=f"Meta MCP: {tool_name}",
                payload=arguments,
                evidence={"policy_reason": decision.reason},
                confidence=0.7,
                risk=decision.risk,
                requires_approval=True,
                status=ActionStatus.PENDING_APPROVAL,
            )
            self.store.save_action(action)
            return {"queued_for_approval": True, "action": action.model_dump(mode="json")}

        if self.transport is None:
            return {"dry_run": True, "tool": tool_name, "arguments": arguments}
        return self.transport(tool_name, arguments)

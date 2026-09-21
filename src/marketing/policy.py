"""Central policy engine. Agents never call paid-media tools directly."""
from __future__ import annotations

from dataclasses import dataclass

from src.marketing.models import RiskLevel


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    risk: RiskLevel
    reason: str


class MarketingPolicy:
    """Fail-closed rules for Google Ads, Meta and outbound messaging."""

    READ_PREFIXES = ("get_", "list_", "search_", "compare_", "export_", "preview_", "diagnose_")
    DESTRUCTIVE_TOKENS = ("delete", "remove", "archive")
    SPEND_TOKENS = ("create_campaign", "create_adset", "create_ad_set", "publish", "activate", "resume")
    BUDGET_TOKENS = ("budget", "bid", "spend", "schedule")

    def __init__(self, monthly_budget_ils: float = 5000, max_budget_increase_pct: float = 10):
        self.monthly_budget_ils = monthly_budget_ils
        self.max_budget_increase_pct = max_budget_increase_pct

    def evaluate(self, provider: str, operation: str, payload: dict | None = None) -> PolicyDecision:
        payload = payload or {}
        op = operation.lower()

        if any(token in op for token in self.DESTRUCTIVE_TOKENS):
            return PolicyDecision(False, True, RiskLevel.CRITICAL, "מחיקה אוטומטית חסומה לחלוטין")

        if op.startswith(self.READ_PREFIXES):
            return PolicyDecision(True, False, RiskLevel.LOW, "פעולת קריאה בלבד")

        if provider == "google_ads" and payload.get("existing_campaign", False):
            return PolicyDecision(False, True, RiskLevel.CRITICAL, "קמפיין קיים מוגן מניהול המערכת")

        requested_monthly = float(payload.get("monthly_budget_ils", 0) or 0)
        if requested_monthly > self.monthly_budget_ils:
            return PolicyDecision(False, True, RiskLevel.CRITICAL, "התקציב המבוקש חורג מהתקרה העסקית")

        increase = float(payload.get("budget_increase_pct", 0) or 0)
        if increase > self.max_budget_increase_pct:
            return PolicyDecision(True, True, RiskLevel.HIGH, "הגדלת תקציב מעל 10% מחייבת אישור")

        if any(token in op for token in self.SPEND_TOKENS + self.BUDGET_TOKENS):
            return PolicyDecision(True, True, RiskLevel.HIGH, "פעולה כספית מחייבת אישור אנושי")

        if provider in {"meta", "google_ads", "email", "whatsapp", "wordpress"}:
            return PolicyDecision(True, True, RiskLevel.MEDIUM, "פעולה חיצונית מחייבת אישור")

        return PolicyDecision(True, False, RiskLevel.LOW, "פעולה פנימית בטוחה")

    def require_system_test_name(self, provider: str, name: str) -> bool:
        return provider != "google_ads" or name.startswith("SYSTEM_TEST_")

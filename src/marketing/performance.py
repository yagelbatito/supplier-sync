"""Cross-channel performance scoring with no vanity-metric shortcuts."""
from __future__ import annotations

from dataclasses import dataclass

from src.marketing.models import PerformanceMetric


@dataclass(frozen=True)
class RankedContent:
    content_id: str
    channel: str
    score: float
    view_rate: float
    engagement_rate: float
    conversion_rate: float
    roas: float
    reason: str


class PerformanceAnalyzer:
    """Ranks content using normalized commercial and engagement signals."""

    def score(self, metric: PerformanceMetric) -> RankedContent:
        impressions = max(metric.impressions, 1)
        clicks = max(metric.clicks, 1)
        view_rate = metric.views / impressions
        engagement_rate = metric.engagements / impressions
        conversion_rate = metric.conversions / clicks if metric.clicks else 0
        roas = metric.revenue / metric.spend if metric.spend else (5.0 if metric.revenue else 0.0)

        # Conversion and ROAS dominate; views help discover organic winners but
        # can never make commercially weak content the sole winner.
        score = min(view_rate, 1) * 15 + min(engagement_rate * 10, 1) * 20
        score += min(conversion_rate * 20, 1) * 30 + min(roas / 5, 1) * 35
        reason = (
            f"צפייה {view_rate:.1%}, מעורבות {engagement_rate:.1%}, "
            f"המרה {conversion_rate:.1%}, ROAS {roas:.2f}"
        )
        return RankedContent(metric.content_id, metric.channel, round(score, 2), view_rate,
                             engagement_rate, conversion_rate, roas, reason)

    def rank(self, metrics: list[PerformanceMetric]) -> list[RankedContent]:
        return sorted((self.score(item) for item in metrics), key=lambda item: item.score, reverse=True)

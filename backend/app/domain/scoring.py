"""Qualification scoring rubric -> score 0-100 + tier (hot/warm/nurture/unqualified)."""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import ScoringRules

TIER_HOT = "hot"
TIER_WARM = "warm"
TIER_NURTURE = "nurture"
TIER_UNQUALIFIED = "unqualified"


@dataclass
class ScoreResult:
    score: int
    tier: str
    breakdown: dict


def _timeline_bucket(months: int | None) -> str:
    if months is None:
        return "unknown"
    if months <= 3:
        return "0-3"
    if months <= 6:
        return "3-6"
    if months <= 12:
        return "6-12"
    return "12+"


def _budget_points(rules: ScoringRules, budget_usd: float | None) -> int:
    if budget_usd is None:
        return 0
    for band in rules.budget_bands:
        if budget_usd >= (band.min_usd or 0) and (band.max_usd is None or budget_usd < band.max_usd):
            return band.points
    return 0


def score_lead(
    *,
    budget_usd: float | None,
    timeline_months: int | None,
    readiness: str | None,
    replies_count: int = 0,
    rules: ScoringRules | None = None,
) -> ScoreResult:
    rules = rules or ScoringRules()
    b = _budget_points(rules, budget_usd)
    t = rules.timeline_points.get(_timeline_bucket(timeline_months), 0)
    r = rules.readiness_points.get(readiness or "general", rules.readiness_points.get("general", 0))
    e = min(replies_count * rules.engagement_points_per_reply, rules.engagement_points_max)
    total = b + t + r + e

    thresholds = rules.tier_thresholds
    if total >= thresholds.get("hot", 70):
        tier = TIER_HOT
    elif total >= thresholds.get("warm", 40):
        tier = TIER_WARM
    elif total >= thresholds.get("nurture", 20):
        tier = TIER_NURTURE
    else:
        tier = TIER_UNQUALIFIED
    return ScoreResult(total, tier, {"budget": b, "timeline": t, "readiness": r, "engagement": e})

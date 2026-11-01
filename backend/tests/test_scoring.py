"""Qualification rubric -> score + tier."""
from __future__ import annotations

from app.domain.scoring import score_lead


def test_hot_lead():
    r = score_lead(budget_usd=250_000, timeline_months=2, readiness="meeting_requested")
    assert r.score == 100
    assert r.tier == "hot"
    assert r.breakdown == {"budget": 40, "timeline": 30, "readiness": 30, "engagement": 0}


def test_warm_lead():
    r = score_lead(budget_usd=150_000, timeline_months=4, readiness="pricing_question")
    assert r.score == 60
    assert r.tier == "warm"


def test_nurture_via_engagement():
    r = score_lead(budget_usd=30_000, timeline_months=None, readiness="general", replies_count=2)
    assert r.score == 20  # 5 (budget) + 5 (general) + 10 (2 replies)
    assert r.tier == "nurture"


def test_unqualified_with_no_data():
    r = score_lead(budget_usd=None, timeline_months=None, readiness=None)
    assert r.score == 5  # general readiness default
    assert r.tier == "unqualified"


def test_engagement_capped_at_15():
    r = score_lead(budget_usd=None, timeline_months=None, readiness="general", replies_count=10)
    assert r.breakdown["engagement"] == 15


def test_budget_band_edges():
    assert score_lead(budget_usd=200_000, timeline_months=None, readiness=None).breakdown["budget"] == 40
    assert score_lead(budget_usd=199_999, timeline_months=None, readiness=None).breakdown["budget"] == 25
    assert score_lead(budget_usd=50_000, timeline_months=None, readiness=None).breakdown["budget"] == 10
    assert score_lead(budget_usd=49_999, timeline_months=None, readiness=None).breakdown["budget"] == 5
    assert score_lead(budget_usd=10_000, timeline_months=None, readiness=None).breakdown["budget"] == 5


def test_timeline_buckets():
    assert score_lead(budget_usd=None, timeline_months=3, readiness=None).breakdown["timeline"] == 30
    assert score_lead(budget_usd=None, timeline_months=6, readiness=None).breakdown["timeline"] == 20
    assert score_lead(budget_usd=None, timeline_months=12, readiness=None).breakdown["timeline"] == 10
    assert score_lead(budget_usd=None, timeline_months=24, readiness=None).breakdown["timeline"] == 5
    assert score_lead(budget_usd=None, timeline_months=None, readiness=None).breakdown["timeline"] == 0


def test_custom_rules_json_shape():
    from app.core.config import ScoringRules

    rules = ScoringRules(tier_thresholds={"hot": 50, "warm": 30, "nurture": 10})
    r = score_lead(budget_usd=120_000, timeline_months=None, readiness="general", rules=rules)
    assert r.score == 30  # 25 budget + 5 general
    assert r.tier == "warm"  # meets warm(30), below hot(50)

"""Extraction + fallback agent + FAQ retrieval (mock LLM path)."""
from __future__ import annotations

import pytest

from app.services.agent import (
    FAQKnowledge,
    detect_escalation,
    detect_readiness,
    extract_budget_usd,
    extract_timeline_months,
)


def test_budget_dollars():
    assert extract_budget_usd("$250k") == 250_000
    assert extract_budget_usd("I have around $250K to invest") == 250_000
    assert extract_budget_usd("maybe 1.5m") == 1_500_000
    assert extract_budget_usd("$500,000") == 500_000
    assert extract_budget_usd("500 thousand dollars") == 500_000
    assert extract_budget_usd("half a million") == 500_000


def test_budget_aed_converted_to_usd():
    usd = extract_budget_usd("AED 2m")
    assert usd == pytest.approx(2_000_000 / 3.6725, rel=0.01)
    assert extract_budget_usd("AED 500,000") == pytest.approx(500_000 / 3.6725, rel=0.01)


def test_budget_none():
    assert extract_budget_usd("I love real estate") is None
    assert extract_budget_usd("") is None


def test_timeline():
    assert extract_timeline_months("within 3 months") == 3
    assert extract_timeline_months("3-6 months") == 3
    assert extract_timeline_months("next year") == 12
    assert extract_timeline_months("ASAP") == 1
    assert extract_timeline_months("next month") == 1
    assert extract_timeline_months("two months") == 2
    assert extract_timeline_months("2 years") == 24
    assert extract_timeline_months("just curious") is None


def test_readiness_detection():
    assert detect_readiness("can we book a call tomorrow?") == "meeting_requested"
    assert detect_readiness("what's the price per sqft?") == "pricing_question"
    assert detect_readiness("please call me back") == "call_requested"
    assert detect_readiness("sounds interesting") == "general"


def test_escalation_detection():
    assert detect_escalation("I want to talk to a human")
    assert detect_escalation("can I speak to someone from your team")
    assert not detect_escalation("I love Dubai real estate")


def test_faq_retrieval():
    kb = FAQKnowledge.load_default()
    assert len(kb.sections) >= 10
    hit = kb.answer("How do I get the golden visa?")
    assert hit is not None and "Golden Visa" in hit[0]
    hit = kb.answer("are there property taxes in dubai?")
    assert hit is not None and "Taxes" in hit[0]
    hit = kb.answer("do you have payment plans?")
    assert hit is not None and "Payment plans" in hit[0]


def test_faq_no_match_for_noise():
    kb = FAQKnowledge.load_default()
    assert kb.answer("asdf qwerty zzzz") is None
    assert kb.answer("a") is None

"""Milestone gate: lifecycle transitions."""
from __future__ import annotations

import pytest

from app.domain import state_machine as sm


def test_legal_transitions():
    assert sm.can_transition(sm.NEW, sm.CONTACTED)
    assert sm.can_transition(sm.CONTACTED, sm.ENGAGED)
    assert sm.can_transition(sm.ENGAGED, sm.QUALIFIED)
    assert sm.can_transition(sm.QUALIFIED, sm.MEETING_BOOKED)
    assert sm.can_transition(sm.MEETING_BOOKED, sm.SHOWED)
    assert sm.can_transition(sm.MEETING_BOOKED, sm.MEETING_BOOKED)  # reschedule
    assert sm.can_transition(sm.NO_SHOW, sm.ENGAGED)  # rebooking
    assert sm.can_transition(sm.LOST, sm.ENGAGED)  # re-engagement


def test_illegal_transitions():
    assert not sm.can_transition(sm.NEW, sm.WON)
    assert not sm.can_transition(sm.NEW, sm.SHOWED)
    assert not sm.can_transition(sm.WON, sm.CONTACTED)
    assert not sm.can_transition(sm.OPTED_OUT, sm.CONTACTED)


def test_assert_transition_raises():
    with pytest.raises(sm.InvalidTransition):
        sm.assert_transition(sm.WON, sm.ENGAGED)


def test_terminal_states():
    for status in (sm.WON, sm.LOST, sm.OPTED_OUT, sm.DISQUALIFIED):
        assert not sm.is_active(status)
    assert sm.is_active(sm.QUALIFIED)
    assert sm.is_active(sm.MEETING_BOOKED)


def test_followup_eligibility():
    assert sm.is_followup_eligible(sm.CONTACTED)
    assert sm.is_followup_eligible(sm.NURTURE)
    assert sm.is_followup_eligible(sm.QUALIFIED)  # booking nudges
    assert not sm.is_followup_eligible(sm.ENGAGED)  # conversation-driven now
    assert not sm.is_followup_eligible(sm.WON)


def test_opt_out_allowed_from_every_active_status():
    for status in sm.ALL_STATUSES:
        if status == sm.OPTED_OUT:
            continue
        if status in sm.TERMINAL and status != sm.DISQUALIFIED:
            continue  # won/lost are terminal; opt-out there is handled directly
        assert sm.can_transition(status, sm.OPTED_OUT), f"missing opt-out from {status}"

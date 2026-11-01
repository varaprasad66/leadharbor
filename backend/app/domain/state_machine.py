"""Lead lifecycle state machine. Every status change goes through here (audited upstream)."""
from __future__ import annotations

NEW = "new"
CONTACTED = "contacted"
ENGAGED = "engaged"
QUALIFIED = "qualified"
NURTURE = "nurture"
DISQUALIFIED = "disqualified"
MEETING_BOOKED = "meeting_booked"
SHOWED = "showed"
NO_SHOW = "no_show"
WON = "won"
LOST = "lost"
OPTED_OUT = "opted_out"

ALL_STATUSES = [
    NEW, CONTACTED, ENGAGED, QUALIFIED, NURTURE, DISQUALIFIED,
    MEETING_BOOKED, SHOWED, NO_SHOW, WON, LOST, OPTED_OUT,
]

TERMINAL = {WON, LOST, OPTED_OUT, DISQUALIFIED}

TRANSITIONS: dict[str, set[str]] = {
    NEW: {CONTACTED, ENGAGED, DISQUALIFIED, OPTED_OUT},
    CONTACTED: {ENGAGED, QUALIFIED, NURTURE, DISQUALIFIED, OPTED_OUT},
    ENGAGED: {QUALIFIED, NURTURE, MEETING_BOOKED, DISQUALIFIED, OPTED_OUT},
    QUALIFIED: {MEETING_BOOKED, NURTURE, ENGAGED, DISQUALIFIED, OPTED_OUT},
    NURTURE: {ENGAGED, QUALIFIED, DISQUALIFIED, OPTED_OUT},
    DISQUALIFIED: {ENGAGED, QUALIFIED, OPTED_OUT},  # requalification allowed
    MEETING_BOOKED: {MEETING_BOOKED, SHOWED, NO_SHOW, LOST, WON, OPTED_OUT},  # self = reschedule
    SHOWED: {WON, LOST, MEETING_BOOKED, OPTED_OUT},  # follow-up meeting possible
    NO_SHOW: {ENGAGED, MEETING_BOOKED, LOST, OPTED_OUT},
    WON: set(),
    LOST: {ENGAGED},  # re-engagement allowed
    OPTED_OUT: set(),
}


class InvalidTransition(Exception):
    pass


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def assert_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(f"illegal transition: {current!r} -> {target!r}")


def is_followup_eligible(status: str) -> bool:
    """Auto follow-ups run for these statuses; ENGAGED+ is conversation-driven."""
    return status in {CONTACTED, NURTURE, QUALIFIED}


def is_active(status: str) -> bool:
    return status not in TERMINAL

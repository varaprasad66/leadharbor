"""Consent gating, STOP/HELP parsing, CAN-SPAM footer, FB consent mapping."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.domain.consent import (
    CONSENT_FORM_LANGUAGE,
    GuardDecision,
    can_call,
    can_send_email,
    can_send_sms,
    email_footer,
    parse_consent_from_fb_fields,
    parse_inbound_sms_command,
)


def lead(**kw):
    base = dict(
        opt_out=False, consent_sms=True, consent_email=True, consent_call=True,
        timezone="America/New_York",
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ----------------------------------------------------------------- STOP / HELP
def test_stop_variants():
    for body in ("STOP", "stop", "STOP.", "stop all", "Stop All", "Unsubscribe", "CANCEL", "end", "quit"):
        assert parse_inbound_sms_command(body) == "stop", body


def test_help_variants():
    for body in ("HELP", "help me", "info"):
        assert parse_inbound_sms_command(body) == "help"


def test_normal_message_is_not_a_command():
    assert parse_inbound_sms_command("How much is a studio in JVC?") is None
    assert parse_inbound_sms_command("") is None
    assert parse_inbound_sms_command("cancellation fee?") is None  # only first word counts


# ------------------------------------------------------------------ SMS guard
def test_sms_blocked_without_consent():
    d = can_send_sms(lead(consent_sms=False), datetime(2026, 1, 15, 15, 0))
    assert d.allowed is False and d.reason == "no_consent"


def test_sms_blocked_when_opted_out():
    d = can_send_sms(lead(opt_out=True), datetime(2026, 1, 15, 15, 0))
    assert d.allowed is False and d.reason == "opted_out"


def test_sms_blocked_in_quiet_hours_with_deferral():
    # 03:00 UTC = 22:00 EST -> quiet
    d = can_send_sms(lead(), datetime(2026, 1, 15, 3, 0))
    assert d.allowed is False and d.reason == "quiet_hours"
    assert d.defer_until == datetime(2026, 1, 15, 13, 0)  # 08:00 EST


def test_sms_allowed_during_day():
    d = can_send_sms(lead(), datetime(2026, 1, 15, 15, 0))
    assert d.allowed is True and d.defer_until is None


def test_email_guard_ignores_quiet_hours():
    assert can_send_email(lead(), ).allowed is True
    d = can_send_email(lead(consent_email=False))
    assert d.reason == "no_consent"


def test_call_guard_requires_explicit_consent():
    assert can_call(lead(consent_call=False)).reason == "no_consent"
    assert can_call(lead()).allowed is True


# --------------------------------------------------------------- FB consent map
def test_generic_consent_checkbox_grants_all_channels():
    fields = [{"name": "consent", "values": ["Agree"]}]
    assert parse_consent_from_fb_fields(fields) == {
        "consent_sms": True, "consent_email": True, "consent_call": True,
    }


def test_channel_specific_consent_fields():
    fields = [
        {"name": "sms_consent", "values": ["yes"]},
        {"name": "email_opt_in", "values": ["yes"]},
    ]
    result = parse_consent_from_fb_fields(fields)
    assert result["consent_sms"] is True
    assert result["consent_email"] is True
    assert result["consent_call"] is False  # calls need explicit opt-in


def test_missing_consent_defaults_safe():
    result = parse_consent_from_fb_fields([{"name": "full_name", "values": ["x"]}])
    assert result == {"consent_sms": False, "consent_email": False, "consent_call": False}


def test_consent_form_language_covers_tcpa_basics():
    lower = CONSENT_FORM_LANGUAGE.lower()
    assert "stop" in lower
    assert "not a condition of purchase" in lower
    assert "prerecorded" in lower or "ai" in lower
    assert "msg&data rates" in lower


def test_email_footer_has_address_and_unsubscribe():
    footer = email_footer("https://api.example.com", "123 Main St, Dubai", lead_id=42)
    assert "123 Main St" in footer
    assert "https://api.example.com/unsubscribe/42" in footer

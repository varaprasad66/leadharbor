"""Consent, opt-out and messaging guard. TCPA/CAN-SPAM enforcement point.

Policy (documented in docs/COMPLIANCE.md):
- Marketing SMS/calls require recorded consent AND a non-opted-out lead.
- Replies to an inbound conversation are user-initiated and may be answered
  immediately, but marketing follow-ups always respect quiet hours.
- Email requires consent (or legitimate interest per client policy) and a
  CAN-SPAM footer with a physical address + unsubscribe mechanism.
- STOP-family keywords opt out instantly across every channel.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

STOP_KEYWORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
HELP_KEYWORDS = {"help", "info"}


@dataclass
class GuardDecision:
    allowed: bool
    reason: str | None = None  # no_consent | opted_out | quiet_hours
    defer_until: datetime | None = None


def parse_inbound_sms_command(body: str) -> str | None:
    """Return 'stop' | 'help' | None based on the first word (Twilio-standard set)."""
    if not body:
        return None
    first = body.strip().split()[0].lower().strip(".,!?:;")
    if first in STOP_KEYWORDS:
        return "stop"
    if first in HELP_KEYWORDS:
        return "help"
    return None


def can_send_sms(
    lead,
    now_utc: datetime,
    quiet_start: int = 21,
    quiet_end: int = 8,
) -> GuardDecision:
    if lead.opt_out:
        return GuardDecision(False, "opted_out")
    if not lead.consent_sms:
        return GuardDecision(False, "no_consent")
    from app.domain.quiet_hours import is_quiet_hours, next_send_window

    if is_quiet_hours(now_utc, lead.timezone, quiet_start, quiet_end):
        return GuardDecision(
            False, "quiet_hours", defer_until=next_send_window(now_utc, lead.timezone, quiet_start, quiet_end)
        )
    return GuardDecision(True)


def can_send_email(lead) -> GuardDecision:
    if lead.opt_out:
        return GuardDecision(False, "opted_out")
    if not lead.consent_email:
        return GuardDecision(False, "no_consent")
    return GuardDecision(True)


def can_call(lead) -> GuardDecision:
    """AI voice calls require explicit call consent (FCC: AI voices = robocalls without it)."""
    if lead.opt_out:
        return GuardDecision(False, "opted_out")
    if not lead.consent_call:
        return GuardDecision(False, "no_consent")
    return GuardDecision(True)


def email_footer(public_base_url: str, company_address: str, lead_id: int | None = None) -> str:
    unsub = f"{public_base_url}/unsubscribe/{lead_id}" if lead_id else public_base_url
    return (
        "\n\n---\n"
        f"You are receiving this because you requested information about Dubai real estate.\n"
        f"{company_address}\n"
        f"Unsubscribe: {unsub} (or reply STOP)"
    )


# Exact consent checkbox language for the Facebook/Instagram lead form
# (client question #2). One checkbox covering all channels keeps the form simple;
# per-channel custom questions are also supported by the parser below.
CONSENT_FORM_LANGUAGE = (
    "By submitting, I agree that Meridian Realty Dubai and its agents may contact me by "
    "phone call (including AI/prerecorded voice), text message (SMS), email, and WhatsApp "
    "at the number and email provided, with offers and information about Dubai real estate. "
    "Consent is not a condition of purchase. Message frequency varies; msg&data rates may "
    "apply. Reply STOP to opt out, HELP for help."
)


def parse_consent_from_fb_fields(field_data: list[dict], default: bool = False) -> dict[str, bool]:
    """Map FB lead field_data [{name, values}] to per-channel consent booleans."""
    consent = {"consent_sms": default, "consent_email": default, "consent_call": default}
    generic_consent = False
    channel_words = ("sms", "text", "email", "call", "voice", "whatsapp", "phone")
    for field in field_data or []:
        name = str(field.get("name", "")).lower()
        value = " ".join(str(v).lower() for v in field.get("values", []))
        positive = value in {"yes", "true", "agree", "agreed", "i agree", "on", "1", "checked"}
        if not positive:
            continue
        # generic consent = a consent/agree field that names NO specific channel
        if not any(w in name for w in channel_words) and any(
            w in name for w in ("consent", "agree", "tcpa", "permission")
        ):
            generic_consent = True
        if any(w in name for w in ("sms", "text", "phone")):
            consent["consent_sms"] = True
        if "email" in name:
            consent["consent_email"] = True
        if any(w in name for w in ("call", "voice", "whatsapp")):
            consent["consent_call"] = True
    if generic_consent:
        consent = {k: True for k in consent}
    return consent

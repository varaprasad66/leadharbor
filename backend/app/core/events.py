"""Event-sourcing: every meaningful action becomes an Event row (drives analytics)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.core.timeutils import utcnow

if TYPE_CHECKING:
    from app.models import Event

# --- Event type constants ---
LEAD_CREATED = "lead_created"
CONSENT_RECORDED = "consent_recorded"
DUPLICATE_SUPPRESSED = "duplicate_suppressed"
SMS_QUEUED = "sms_queued"            # deferred by quiet hours
SMS_SENT = "sms_sent"
SMS_SKIPPED = "sms_skipped"
SMS_RECEIVED = "sms_received"
EMAIL_SENT = "email_sent"
EMAIL_SKIPPED = "email_skipped"
EMAIL_RECEIVED = "email_received"
CALL_SCHEDULED = "call_scheduled"
CALL_COMPLETED = "call_completed"
REPLY_HANDLED = "reply_handled"
QUALIFICATION_UPDATED = "qualification_updated"
ESCALATED = "escalated"
FOLLOWUP_SENT = "followup_sent"
FOLLOWUP_EXHAUSTED = "followup_exhausted"
REMINDER_SENT = "reminder_sent"
APPOINTMENT_BOOKED = "appointment_booked"
APPOINTMENT_STATUS_CHANGED = "appointment_status_changed"
OPT_OUT = "opt_out"
HELP_REPLY = "help_reply"
STAGE_CHANGED = "stage_changed"
CRM_SYNCED = "crm_synced"
CRM_SYNC_FAILED = "crm_sync_failed"
TEAM_ALERTED = "team_alerted"
LLM_ERROR = "llm_error"
UNMATCHED_INBOUND = "unmatched_inbound"
ERROR = "error"


def log_event(
    session: Session,
    event_type: str,
    lead_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> "Event":
    from app.models import Event

    event = Event(type=event_type, lead_id=lead_id, payload=payload or {}, created_at=utcnow())
    session.add(event)
    return event

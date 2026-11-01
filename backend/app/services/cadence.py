"""Cadence runner: deferred-message flush, AI follow-up sequences, reminders, no-shows.

n8n calls POST /api/v1/cadence/run every 15 minutes. Everything here is idempotent.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session, joinedload

from app.core.config import BOOKING_NUDGE_STEPS, Settings
from app.core.events import (
    FOLLOWUP_EXHAUSTED,
    FOLLOWUP_SENT,
    REMINDER_SENT,
    SMS_QUEUED,
    log_event,
)
from app.core.logging import get_logger
from app.core.timeutils import to_local, utcnow
from app.domain import state_machine as sm
from app.domain.consent import email_footer
from app.models import Appointment, Lead, Message
from app.services.agent import AgentService, render_email
from app.services.flow import transition_lead
from app.services.messenger import LeadMessenger
from app.services.notifier import Notifier

log = get_logger("leadharbor.cadence")

NO_SHOW_GRACE_MINUTES = 120


def _flush_deferred(session: Session, messenger: LeadMessenger, now) -> int:
    """Send deferred (quiet-hours queued) messages whose time has come.

    The quiet-hours decision was made when the message was scheduled, so the flush
    only re-checks consent and opt-out — otherwise late-night schedules would defer forever.
    """
    sent = 0
    due = (
        session.query(Message)
        .filter(Message.status == "deferred", Message.channel == "sms",
                Message.scheduled_at.isnot(None), Message.scheduled_at <= now)
        .limit(200)
        .all()
    )
    for msg in due:
        lead = session.get(Lead, msg.lead_id)
        if lead is None or lead.opt_out:
            msg.status = "failed"
            continue
        result = messenger.send_sms(session, lead, msg.body, enforce_quiet_hours=False)
        # send_sms created a NEW message row; retire the deferred placeholder
        msg.status = "superseded"
        if result.get("status") == "sent":
            sent += 1
    session.flush()
    return sent


def _appointment_maintenance(
    session: Session,
    settings: Settings,
    messenger: LeadMessenger,
    notifier: Notifier,
    now,
) -> dict:
    counts = {"reminders": 0, "no_shows": 0}
    # lookback window covers no-show detection; reminders only fire for future slots
    upcoming = (
        session.query(Appointment)
        .options(joinedload(Appointment.lead))
        .filter(
            Appointment.status == "booked",
            Appointment.scheduled_at > now - timedelta(days=2),
        )
        .all()
    )
    for appt in upcoming:
        lead: Lead = appt.lead
        if lead.opt_out:
            continue

        # auto no-show after grace period
        if now > appt.scheduled_at + timedelta(minutes=NO_SHOW_GRACE_MINUTES):
            appt.status = "no_show"
            transition_lead(session, lead, sm.NO_SHOW, reason="auto_no_show")
            # rebooking sequence: back to engaged + nudge
            transition_lead(session, lead, sm.ENGAGED, reason="rebooking_sequence")
            lead.next_followup_at = now + timedelta(hours=2)
            messenger.send_sms(session, lead, _fmt_reminder(settings, lead, "rebook_sms", None))
            notifier.alert_meeting_no_show(session, lead, appt)
            counts["no_shows"] += 1
            continue

        if appt.scheduled_at <= now:
            continue  # inside the grace window: wait for the meeting

        # 24h reminder
        if not appt.remind_24_sent and now >= appt.scheduled_at - timedelta(hours=24):
            local = to_local(appt.scheduled_at, appt.timezone)
            text = _fmt_reminder(settings, lead, "reminder_24_sms", local)
            result = messenger.send_sms(session, lead, text)
            if result.get("status") == "deferred":
                continue  # retry next run inside the send window
            appt.remind_24_sent = True
            if result.get("status") == "sent":
                counts["reminders"] += 1
                log_event(session, REMINDER_SENT, lead.id, {"kind": "24h", "appointment_id": appt.id})

        # 1h reminder
        if not appt.remind_1_sent and now >= appt.scheduled_at - timedelta(hours=1):
            text = _fmt_reminder(settings, lead, "reminder_1_sms", None)
            result = messenger.send_sms(session, lead, text)
            if result.get("status") == "deferred":
                continue
            appt.remind_1_sent = True
            if result.get("status") == "sent":
                counts["reminders"] += 1
                log_event(session, REMINDER_SENT, lead.id, {"kind": "1h", "appointment_id": appt.id})

    session.flush()
    return counts


def _fmt_reminder(settings: Settings, lead: Lead, template: str, local_time) -> str:
    from app.services.agent import TEMPLATES, _fmt

    text = _fmt(TEMPLATES[template], lead, settings)
    if local_time is not None:
        text = text.replace(
            "{local_time}", f"{local_time:%H:%M} ({lead.timezone.split('/')[-1]})"
        )
    return text


def run_cadence(session: Session, container) -> dict:
    settings: Settings = container.settings
    messenger: LeadMessenger = container.messenger
    agent: AgentService = container.agent
    notifier: Notifier = container.notifier
    now = utcnow()

    summary: dict = {"deferred_sent": 0, "followups_sent": 0, "followups_deferred": 0,
                     "followups_skipped": 0, "exhausted": 0, "reminders": 0, "no_shows": 0}

    summary["deferred_sent"] = _flush_deferred(session, messenger, now)
    maint = _appointment_maintenance(session, settings, messenger, notifier, now)
    summary["reminders"] = maint["reminders"]
    summary["no_shows"] = maint["no_shows"]

    eligible = (
        session.query(Lead)
        .filter(
            Lead.opt_out.is_(False),
            Lead.status.in_(["contacted", "nurture", "qualified"]),
            Lead.next_followup_at.isnot(None),
            Lead.next_followup_at <= now,
        )
        .limit(500)
        .all()
    )
    for lead in eligible:
        steps = BOOKING_NUDGE_STEPS if lead.status == sm.QUALIFIED else settings.cadence_steps
        if lead.touch_count >= len(steps) or lead.touch_count >= settings.max_followups:
            lead.next_followup_at = None
            log_event(session, FOLLOWUP_EXHAUSTED, lead.id, {"touch_count": lead.touch_count})
            summary["exhausted"] += 1
            continue

        step = steps[lead.touch_count]
        if step.channel == "sms":
            body = agent.personalize_followup(session, lead, step.template)
            result = messenger.send_sms(session, lead, body)
        else:
            subject, body = render_email(lead, step.template, settings)
            result = messenger.send_email(session, lead, subject, body)

        lead.touch_count += 1
        if result.get("status") == "deferred":
            summary["followups_deferred"] += 1
            log_event(session, SMS_QUEUED, lead.id, {"step": step.step})
        elif result.get("status") == "sent":
            summary["followups_sent"] += 1
            log_event(session, FOLLOWUP_SENT, lead.id,
                      {"step": step.step, "channel": step.channel, "template": step.template})
        else:
            summary["followups_skipped"] += 1
            lead.next_followup_at = None  # no consent / hard stop: stop the sequence
            continue

        if lead.touch_count < len(steps):
            lead.next_followup_at = now + timedelta(hours=steps[lead.touch_count].after_hours)
        else:
            lead.next_followup_at = None

    session.flush()
    return summary

"""Milestone 3: follow-up sequences, quiet-hours deferral, exhaustion, booking nudges."""
from __future__ import annotations

from datetime import timedelta

from app.core.timeutils import utcnow
from app.models import Lead, Message
from tests.conftest import auth_headers, make_lead, tz_for_local_hour


def make_due(ctx, **lead_kw) -> int:
    lead = make_lead(ctx.client, **lead_kw)
    session = ctx.container.sessionmaker()
    db_lead = session.get(Lead, lead["id"])
    db_lead.next_followup_at = utcnow() - timedelta(minutes=1)
    session.commit()
    session.close()
    return lead["id"]


def test_followup_sent_when_due(ctx):
    lead_id = make_due(ctx)
    r = ctx.client.post("/api/v1/cadence/run", headers=auth_headers())
    summary = r.json()["summary"]
    assert summary["followups_sent"] == 1

    session = ctx.container.sessionmaker()
    lead = session.get(Lead, lead_id)
    assert lead.touch_count == 1
    assert lead.next_followup_at is not None          # step 2 scheduled (+24h)
    assert (lead.next_followup_at - utcnow()).total_seconds() > 23 * 3600
    session.close()

    # second message is the SMS follow-up
    assert len(ctx.sent.twilio) == 2                  # instant + followup
    followup = ctx.sent.twilio[1].read().decode()
    assert "15551230000" in followup


def test_not_due_no_send(ctx):
    lead = make_lead(ctx.client)
    session = ctx.container.sessionmaker()
    db_lead = session.get(Lead, lead["id"])
    db_lead.next_followup_at = utcnow() + timedelta(hours=2)
    session.commit()
    session.close()
    r = ctx.client.post("/api/v1/cadence/run", headers=auth_headers())
    assert r.json()["summary"]["followups_sent"] == 0


def test_engaged_lead_left_alone(ctx):
    """A lead that replied is conversation-driven: cadence must not touch it."""
    lead_id = make_due(ctx)
    session = ctx.container.sessionmaker()
    db_lead = session.get(Lead, lead_id)
    db_lead.status = "engaged"
    session.commit()
    session.close()
    r = ctx.client.post("/api/v1/cadence/run", headers=auth_headers())
    assert r.json()["summary"]["followups_sent"] == 0


def test_sequence_exhaustion(ctx):
    lead_id = make_due(ctx)
    session = ctx.container.sessionmaker()
    db_lead = session.get(Lead, lead_id)
    db_lead.touch_count = 5  # default cadence has 5 steps
    session.commit()
    session.close()
    r = ctx.client.post("/api/v1/cadence/run", headers=auth_headers())
    assert r.json()["summary"]["exhausted"] == 1
    session = ctx.container.sessionmaker()
    lead = session.get(Lead, lead_id)
    assert lead.next_followup_at is None
    session.close()


def test_qualified_lead_gets_booking_nudge(ctx):
    lead_id = make_due(ctx)
    session = ctx.container.sessionmaker()
    db_lead = session.get(Lead, lead_id)
    db_lead.status = "qualified"
    db_lead.touch_count = 0
    session.commit()
    session.close()

    ctx.client.post("/api/v1/cadence/run", headers=auth_headers())
    followup = ctx.sent.twilio[1].read().decode()
    assert "qualify" in followup.lower()
    assert "cal.com" in followup  # contains the booking link


def test_email_step_uses_sendgrid(ctx):
    lead_id = make_due(ctx)
    session = ctx.container.sessionmaker()
    db_lead = session.get(Lead, lead_id)
    db_lead.touch_count = 1  # step 2 = email
    session.commit()
    session.close()
    ctx.client.post("/api/v1/cadence/run", headers=auth_headers())
    assert len(ctx.sent.sendgrid) == 2  # instant + followup email
    assert len(ctx.sent.twilio) == 1   # no extra SMS


def test_quiet_hours_defer_followup_then_flush(ctx):
    lead_id = make_due(ctx)
    session = ctx.container.sessionmaker()
    lead = session.get(Lead, lead_id)
    lead.timezone = tz_for_local_hour(22)  # quiet hours right now
    session.commit()
    session.close()

    r = ctx.client.post("/api/v1/cadence/run", headers=auth_headers())
    summary = r.json()["summary"]
    assert summary["followups_deferred"] == 1
    assert summary["followups_sent"] == 0

    # force the deferred message due, run again -> flushed
    session = ctx.container.sessionmaker()
    msg = session.query(Message).filter(Message.status == "deferred").one()
    msg.scheduled_at = utcnow() - timedelta(minutes=1)
    session.commit()
    session.close()

    r = ctx.client.post("/api/v1/cadence/run", headers=auth_headers())
    assert r.json()["summary"]["deferred_sent"] == 1


def test_opted_out_lead_never_followed_up(ctx):
    lead_id = make_due(ctx)
    session = ctx.container.sessionmaker()
    lead = session.get(Lead, lead_id)
    lead.opt_out = True
    session.commit()
    session.close()
    r = ctx.client.post("/api/v1/cadence/run", headers=auth_headers())
    assert r.json()["summary"]["followups_sent"] == 0

"""Milestone 4: booking, reminders, no-show handling."""
from __future__ import annotations

from datetime import timedelta

from app.core.timeutils import utcnow
from app.models import Appointment, Lead
from tests.conftest import auth_headers, make_lead


def book(ctx, lead_id, hours_ahead=2, **overrides) -> dict:
    payload = {
        "lead_id": lead_id,
        "scheduled_at": (utcnow() + timedelta(hours=hours_ahead)).isoformat(),
        "timezone": "America/New_York",
        "meeting_url": "https://zoom.us/j/123",
        "provider": "calcom",
    }
    payload.update(overrides)
    r = ctx.client.post("/api/v1/appointments", json=payload, headers=auth_headers())
    assert r.status_code == 201, r.text
    return r.json()


def test_booking_moves_lead_to_meeting_booked(ctx):
    lead = make_lead(ctx.client)
    appt = book(ctx, lead["id"])
    assert appt["status"] == "booked"

    lead_now = ctx.client.get(f"/api/v1/leads/{lead['id']}", headers=auth_headers()).json()
    assert lead_now["status"] == "meeting_booked"
    assert lead_now["meeting_at"] is not None
    assert lead_now["next_followup_at"] is None  # booking supersedes follow-ups

    timeline = ctx.client.get(f"/api/v1/leads/{lead['id']}/timeline", headers=auth_headers()).json()
    assert "appointment_booked" in {i["type"] for i in timeline}


def test_booking_from_fresh_lead_works(ctx):
    """Calendar webhook may arrive while lead is still 'new' — pipeline guides it through."""
    lead = make_lead(ctx.client)
    session = ctx.container.sessionmaker()
    session.get(Lead, lead["id"]).status = "new"
    session.commit()
    session.close()
    appt = book(ctx, lead["id"])
    assert appt["status"] == "booked"


def test_booking_unknown_lead_404(ctx):
    r = ctx.client.post(
        "/api/v1/appointments",
        json={"lead_id": 99999, "scheduled_at": utcnow().isoformat()},
        headers=auth_headers(),
    )
    assert r.status_code == 404


def test_attended_meeting_marks_showed(ctx):
    lead = make_lead(ctx.client)
    appt = book(ctx, lead["id"])
    r = ctx.client.patch(f"/api/v1/appointments/{appt['id']}/status",
                         json={"status": "showed"}, headers=auth_headers())
    assert r.json()["status"] == "showed"
    lead_now = ctx.client.get(f"/api/v1/leads/{lead['id']}", headers=auth_headers()).json()
    assert lead_now["status"] == "showed"


def test_no_show_triggers_rebooking_sequence(ctx):
    lead = make_lead(ctx.client)
    appt = book(ctx, lead["id"])
    r = ctx.client.patch(f"/api/v1/appointments/{appt['id']}/status",
                         json={"status": "no_show"}, headers=auth_headers())
    assert r.status_code == 200

    lead_now = ctx.client.get(f"/api/v1/leads/{lead['id']}", headers=auth_headers()).json()
    assert lead_now["status"] == "engaged"           # rebooking mode
    assert lead_now["next_followup_at"] is not None  # sequence reactivated

    # rebook SMS sent
    rebook = ctx.sent.twilio[-1].read().decode()
    assert "missed" in rebook.lower() or "new time" in rebook.lower()


def test_reminders_sent_by_cadence(ctx):
    lead = make_lead(ctx.client)
    book(ctx, lead["id"], hours_ahead=1)  # both 24h and 1h reminders are due
    r = ctx.client.post("/api/v1/cadence/run", headers=auth_headers())
    summary = r.json()["summary"]
    assert summary["reminders"] == 2

    session = ctx.container.sessionmaker()
    appt = session.query(Appointment).one()
    assert appt.remind_24_sent and appt.remind_1_sent
    session.close()

    bodies = " ".join(req.read().decode() for req in ctx.sent.twilio[1:])
    assert "tomorrow" in bodies.lower() or "hour" in bodies.lower()


def test_auto_no_show_after_grace_period(ctx):
    lead = make_lead(ctx.client)
    appt = book(ctx, lead["id"], hours_ahead=-3)  # 3h in the past, still 'booked'
    r = ctx.client.post("/api/v1/cadence/run", headers=auth_headers())
    assert r.json()["summary"]["no_shows"] == 1

    session = ctx.container.sessionmaker()
    appt = session.get(Appointment, appt["id"])
    assert appt.status == "no_show"
    lead_db = session.get(Lead, lead["id"])
    assert lead_db.status == "engaged"  # rebooking sequence started
    session.close()

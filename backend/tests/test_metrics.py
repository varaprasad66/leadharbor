"""Milestone 4: funnel analytics end-to-end with a known scenario."""
from __future__ import annotations

from app.models import Lead
from tests.conftest import auth_headers, make_lead


def test_funnel_metrics_match_seeded_scenario(ctx):
    # L1: full journey — reply, qualify hot, book, attend
    l1 = make_lead(ctx.client, email="l1@example.com", phone="+15551110001")
    ctx.client.post("/api/v1/webhooks/twilio/sms",
                    data={"From": "+15551110001", "Body": "I have $300k, closing in 2 months, let's book a call"})
    r = ctx.client.post("/api/v1/appointments",
                        json={"lead_id": l1["id"], "scheduled_at": "2027-01-10T15:00:00",
                              "timezone": "America/New_York"}, headers=auth_headers())
    appt_id = r.json()["id"]
    ctx.client.patch(f"/api/v1/appointments/{appt_id}/status", json={"status": "showed"},
                     headers=auth_headers())

    # L2: contacted, never replied
    make_lead(ctx.client, email="l2@example.com", phone="+15551110002")

    # L3: email-only consent -> contacted via email
    make_lead(ctx.client, email="l3@example.com", phone="+15551110003",
              consent_sms=False, consent_email=True)

    m = ctx.client.get("/api/v1/metrics/funnel?days=30", headers=auth_headers()).json()
    assert m["leads_total"] == 3
    assert m["contacted"] == 3
    assert m["replied"] == 1
    assert m["qualified"] == 1
    assert m["booked"] == 1
    assert m["showed"] == 1
    assert m["no_shows"] == 0
    assert m["contact_rate"] == 1.0
    assert m["reply_rate"] == round(1 / 3, 4)
    assert m["qualification_rate"] == 1.0
    assert m["booking_rate"] == 1.0
    assert m["show_rate"] == 1.0
    assert m["avg_time_to_first_contact_seconds"] is not None
    assert m["by_source"] == {"facebook": 3}
    assert m["by_tier"].get("hot") == 1


def test_metrics_requires_api_key(ctx):
    r = ctx.client.get("/api/v1/metrics/funnel")
    assert r.status_code == 401


def test_metrics_empty_window(ctx):
    m = ctx.client.get("/api/v1/metrics/funnel?days=1", headers=auth_headers()).json()
    assert m["leads_total"] == 0
    assert m["reply_rate"] is None  # no division by zero


def test_lead_status_manual_override(ctx):
    lead = make_lead(ctx.client)
    r = ctx.client.patch(f"/api/v1/leads/{lead['id']}/status", json={"status": "nurture"},
                         headers=auth_headers())
    assert r.status_code == 200 and r.json()["status"] == "nurture"
    # illegal jump: nurture -> won
    r = ctx.client.patch(f"/api/v1/leads/{lead['id']}/status", json={"status": "won"},
                         headers=auth_headers())
    assert r.status_code == 409


def test_unsubscribe_link_opts_out(ctx):
    lead = make_lead(ctx.client)
    r = ctx.client.get(f"/api/v1/unsubscribe/{lead['id']}")
    assert r.status_code == 200
    lead_now = ctx.client.get(f"/api/v1/leads/{lead['id']}", headers=auth_headers()).json()
    assert lead_now["opt_out"] is True

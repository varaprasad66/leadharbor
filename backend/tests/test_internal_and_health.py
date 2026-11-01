"""Internal messaging endpoints, call queue, health probes, API-key enforcement."""
from __future__ import annotations

from datetime import timedelta

from app.core.events import CALL_SCHEDULED
from app.core.timeutils import utcnow
from app.models import Event, Lead
from tests.conftest import auth_headers, make_lead


def test_internal_sms_respects_guard(ctx):
    lead = make_lead(ctx.client)
    # opted out -> skipped
    session = ctx.container.sessionmaker()
    session.get(Lead, lead["id"]).opt_out = True
    session.commit()
    session.close()
    r = ctx.client.post("/api/v1/internal/messages/sms",
                        json={"lead_id": lead["id"], "body": "hello"}, headers=auth_headers())
    assert r.json()["status"] == "skipped"
    assert len(ctx.sent.twilio) == 1  # only the original instant SMS


def test_internal_sms_sends_when_allowed(ctx):
    lead = make_lead(ctx.client)
    r = ctx.client.post("/api/v1/internal/messages/sms",
                        json={"lead_id": lead["id"], "body": "quick update!"}, headers=auth_headers())
    assert r.json()["status"] == "sent"
    assert len(ctx.sent.twilio) == 2


def test_internal_email_no_address(ctx):
    lead = make_lead(ctx.client, email=None)
    r = ctx.client.post("/api/v1/internal/messages/email",
                        json={"lead_id": lead["id"], "subject": "s", "body": "b"}, headers=auth_headers())
    assert r.json()["status"] == "skipped"
    assert r.json()["reason"] == "no_email"


def test_internal_requires_api_key(ctx):
    assert ctx.client.get("/api/v1/internal/voice/calls-due").status_code == 401
    assert ctx.client.get("/api/v1/leads").status_code == 401
    assert ctx.client.post("/api/v1/cadence/run").status_code == 401


def test_calls_due_queue(ctx):
    lead = make_lead(ctx.client)  # consent_call=True -> CALL_SCHEDULED event on intake
    # make the call due (scheduled 5min after creation)
    session = ctx.container.sessionmaker()
    evt = session.query(Event).filter(Event.type == CALL_SCHEDULED, Event.lead_id == lead["id"]).one()
    evt.created_at = utcnow() - timedelta(minutes=10)
    session.commit()
    session.close()

    r = ctx.client.get("/api/v1/internal/voice/calls-due", headers=auth_headers()).json()
    assert lead["id"] in [c["lead_id"] for c in r["calls_due"]]

    # after CALL_COMPLETED the lead leaves the queue
    ctx.client.post("/api/v1/webhooks/voice/postcall",
                    json={"lead_id": lead["id"], "transcript": "hello there"})
    r = ctx.client.get("/api/v1/internal/voice/calls-due", headers=auth_headers()).json()
    assert lead["id"] not in [c["lead_id"] for c in r["calls_due"]]


def test_health_and_readiness(ctx):
    r = ctx.client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"

    r = ctx.client.get("/health/ready")
    assert r.status_code == 200
    checks = r.json()["checks"]
    assert checks["database"] == "ok"
    assert checks["twilio"] == "configured"


def test_leads_pagination_and_filters(ctx):
    make_lead(ctx.client, email="a@x.com", phone="+15551110001")
    make_lead(ctx.client, email="b@x.com", phone="+15551110002", full_name="Betty Brown")
    r = ctx.client.get("/api/v1/leads?limit=1", headers=auth_headers())
    assert len(r.json()) == 1
    r = ctx.client.get("/api/v1/leads?status=contacted", headers=auth_headers())
    assert len(r.json()) == 2
    r = ctx.client.get("/api/v1/leads?status=won", headers=auth_headers())
    assert r.json() == []

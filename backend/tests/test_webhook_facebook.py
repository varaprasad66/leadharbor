"""Milestone 1: FB/IG lead capture -> instant SMS + email -> CRM + alerts."""
from __future__ import annotations

from app.models import Lead, Message
from tests.conftest import auth_headers, tz_for_local_hour

FIELD_DATA = [
    {"name": "full_name", "values": ["Sarah Mitchell"]},
    {"name": "email", "values": ["sarah.mitchell@example.com"]},
    {"name": "phone_number", "values": ["(415) 555-0123"]},
    {"name": "city", "values": ["San Diego"]},
    {"name": "state", "values": ["CA"]},
    {"name": "consent", "values": ["Agree"]},
]


def fb_payload(leadgen_id="lead_001", field_data=None):
    return {
        "object": "page",
        "entry": [
            {
                "id": "page_1",
                "time": 1767225600,
                "changes": [
                    {
                        "field": "leadgen",
                        "value": {
                            "leadgen_id": leadgen_id,
                            "form_id": "form_777",
                            "ad_id": "ad_999",
                            "page_id": "page_1",
                            "field_data": field_data if field_data is not None else FIELD_DATA,
                        },
                    }
                ],
            }
        ],
    }


def test_webhook_verification_token(ctx):
    r = ctx.client.get(
        "/api/v1/webhooks/facebook",
        params={"hub.mode": "subscribe", "hub.verify_token": "test-verify-token", "hub.challenge": "CHALLENGE123"},
    )
    assert r.status_code == 200 and r.text == "CHALLENGE123"


def test_webhook_verification_rejects_bad_token(ctx):
    r = ctx.client.get(
        "/api/v1/webhooks/facebook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "X"},
    )
    assert r.status_code == 403


def test_leadgen_creates_lead_with_instant_response(ctx):
    r = ctx.client.post("/api/v1/webhooks/facebook", json=fb_payload())
    assert r.status_code == 200
    assert r.json()["results"][0]["created"] is True

    leads = ctx.client.get("/api/v1/leads", headers=auth_headers()).json()
    assert len(leads) == 1
    lead = leads[0]
    assert lead["full_name"] == "Sarah Mitchell"
    assert lead["phone"] == "+14155550123"            # normalized to E.164
    assert lead["timezone"] == "America/Los_Angeles"  # CA -> Pacific
    assert lead["consent_sms"] and lead["consent_email"] and lead["consent_call"]
    assert lead["status"] == "contacted"              # instant response went out

    # instant SMS + email sent, personalized, compliance-compliant
    assert len(ctx.sent.twilio) == 1
    sms_body = ctx.sent.twilio[0].read().decode()
    assert "Sarah" in sms_body and "STOP" in sms_body
    assert len(ctx.sent.sendgrid) == 1

    # lead created + consent + sms/email events recorded
    timeline = ctx.client.get(f"/api/v1/leads/{lead['id']}/timeline", headers=auth_headers()).json()
    types = {i["type"] for i in timeline}
    assert {"lead_created", "consent_recorded", "sms_sent", "email_sent"} <= types


def test_duplicate_leadgen_suppressed(ctx):
    ctx.client.post("/api/v1/webhooks/facebook", json=fb_payload("dup_1"))
    r = ctx.client.post("/api/v1/webhooks/facebook", json=fb_payload("dup_1"))
    body = r.json()
    assert body["results"][0]["created"] is False
    assert len(ctx.sent.twilio) == 1  # no second instant SMS
    leads = ctx.client.get("/api/v1/leads", headers=auth_headers()).json()
    assert len(leads) == 1


def test_relay_endpoint_for_n8n(ctx):
    payload = {"field_data": FIELD_DATA, "leadgen_id": "relay_1", "source": "instagram"}
    r = ctx.client.post("/api/v1/webhooks/facebook/relay", json=payload, headers=auth_headers())
    assert r.status_code == 200 and r.json()["created"] is True
    lead = ctx.client.get("/api/v1/leads", headers=auth_headers()).json()[0]
    assert lead["source"] == "instagram"


def test_relay_requires_api_key(ctx):
    r = ctx.client.post("/api/v1/webhooks/facebook/relay", json={"field_data": []})
    assert r.status_code == 401


def test_no_consent_means_no_outreach(ctx):
    fields = [f for f in FIELD_DATA if f["name"] != "consent"]
    r = ctx.client.post("/api/v1/webhooks/facebook", json=fb_payload("nc_1", fields))
    assert r.json()["results"][0]["created"] is True
    lead = ctx.client.get("/api/v1/leads", headers=auth_headers()).json()[0]
    assert lead["consent_sms"] is False
    assert lead["status"] == "new"  # stays new, no comms sent
    assert len(ctx.sent.twilio) == 0
    assert len(ctx.sent.sendgrid) == 0


def test_quiet_hours_defer_instant_sms(ctx):
    """Lead processed during their local quiet hours -> SMS deferred, not dropped."""
    from app.services.intake import process_new_lead

    r = ctx.client.post(
        "/api/v1/leads",
        json={
            "full_name": "Quinn Quiet", "email": "quinn@example.com",
            "phone": "+15553334444", "state": "TX",
            "consent_sms": True, "consent_email": True,
        },
        headers=auth_headers(),
    )
    assert r.status_code == 201
    lead_id = r.json()["id"]

    session = ctx.container.sessionmaker()
    lead = session.get(Lead, lead_id)
    lead.timezone = tz_for_local_hour(22)  # it is 22:00 local for this lead right now
    lead.status = "new"
    session.commit()
    actions = process_new_lead(session, ctx.container, lead)
    session.commit()
    session.close()

    assert actions["sms"]["status"] == "deferred"
    assert actions["email"]["status"] == "sent"  # email is never quiet-hour limited
    session = ctx.container.sessionmaker()
    deferred = session.query(Message).filter(Message.status == "deferred").one()
    assert deferred.scheduled_at is not None
    session.close()

"""Milestones 2+3: inbound email replies and AI voice post-call processing."""
from __future__ import annotations

from app.models import Lead
from tests.conftest import auth_headers, make_lead


def test_inbound_email_reply_handled(ctx):
    lead = make_lead(ctx.client, email="mailer@example.com")
    r = ctx.client.post(
        "/api/v1/webhooks/sendgrid/inbound",
        json={
            "from": "Alex Mailer <mailer@example.com>",
            "subject": "Re: Your Dubai investment guide",
            "text": "What is the golden visa requirement?",
        },
    )
    assert r.status_code == 200 and r.json()["action"] == "reply_sent"

    lead_now = ctx.client.get(f"/api/v1/leads/{lead['id']}", headers=auth_headers()).json()
    assert lead_now["status"] == "engaged"
    assert lead_now["replies_count"] == 1

    # agent answered by email
    assert len(ctx.sent.sendgrid) == 2  # instant + reply


def test_inbound_email_unmatched(ctx):
    r = ctx.client.post(
        "/api/v1/webhooks/sendgrid/inbound",
        json={"from": "stranger@nowhere.com", "subject": "hi", "text": "hello"},
    )
    assert r.status_code == 200 and r.json()["matched"] is False


def test_voice_postcall_updates_qualification(ctx):
    lead = make_lead(ctx.client)
    r = ctx.client.post(
        "/api/v1/webhooks/voice/postcall",
        json={
            "lead_id": lead["id"],
            "call_status": "completed",
            "transcript": "The client said their budget is around $300k, they want to purchase "
                          "within 3 months and asked to book a viewing next week.",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["lead_id"] == lead["id"]

    lead_now = ctx.client.get(f"/api/v1/leads/{lead['id']}", headers=auth_headers()).json()
    assert lead_now["status"] == "engaged"
    assert lead_now["budget_usd"] == 300_000
    assert lead_now["timeline_months"] == 3
    assert lead_now["readiness"] == "meeting_requested"
    assert lead_now["tier"] in ("hot", "warm")

    timeline = ctx.client.get(f"/api/v1/leads/{lead['id']}/timeline", headers=auth_headers()).json()
    assert "call_completed" in {i["type"] for i in timeline}


def test_voice_postcall_unmatched(ctx):
    r = ctx.client.post(
        "/api/v1/webhooks/voice/postcall",
        json={"phone": "+19999999999", "transcript": "nobody"},
    )
    assert r.json()["matched"] is False


def test_voice_postcall_human_request_escalates(ctx):
    settings = ctx.settings.model_copy(
        update={"alert_channels": "slack", "slack_webhook_url": "http://slack.test/hook"}
    )
    from app.main import create_app
    from fastapi.testclient import TestClient
    from tests.conftest import capture_transport

    sent_slack = []
    transports = {
        "twilio": capture_transport([], status=201, body={"sid": "S"}),
        "sendgrid": capture_transport([], status=202),
        "slack": capture_transport(sent_slack, status=200, body={"ok": True}),
    }
    with TestClient(create_app(settings, transports)) as client:
        lead = client.post(
            "/api/v1/leads",
            json={"full_name": "Escalating Eddie", "email": "eddie@example.com",
                  "phone": "+15556667777", "consent_sms": True, "consent_email": True},
            headers=auth_headers(),
        ).json()
        r = client.post(
            "/api/v1/webhooks/voice/postcall",
            json={"lead_id": lead["id"], "transcript": "Client asked to speak to a real person."},
        )
        assert r.status_code == 200
        # 2 slack alerts: new-lead alert at intake + the escalation
        assert len(sent_slack) == 2
        assert b"HOT" in sent_slack[-1].read().upper()

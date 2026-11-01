"""Milestone 2: AI qualification over SMS — replies, extraction, STOP/HELP, signatures."""
from __future__ import annotations

from app.core.security import twilio_signature
from app.models import Lead, Message, OptOut
from tests.conftest import Settings, create_app, make_lead
from fastapi.testclient import TestClient


def post_sms(client, from_phone="+15551230000", body="Hi!"):
    return client.post(
        "/api/v1/webhooks/twilio/sms",
        data={"From": from_phone, "To": "+15550001111", "Body": body, "MessageSid": "SM_x"},
    )


def test_inbound_reply_gets_ai_answer_and_updates_qualification(ctx):
    lead = make_lead(ctx.client)
    r = post_sms(ctx.client, body="Hey! I'm looking at around $250k, hoping to buy in 3 months")
    assert r.status_code == 200 and r.json()["action"] == "reply_sent"

    lead_now = ctx.client.get(f"/api/v1/leads/{lead['id']}", headers={"X-API-Key": "test-key"}).json()
    assert lead_now["status"] == "engaged"
    assert lead_now["replies_count"] == 1
    assert lead_now["budget_usd"] == 250_000
    assert lead_now["timeline_months"] == 3
    assert lead_now["score"] > 0

    # agent replied over SMS
    assert len(ctx.sent.twilio) == 2  # instant + reply
    reply_body = ctx.sent.twilio[1].read().decode()
    assert "15551230000" in reply_body

    # events: received + reply handled + qualification updated
    timeline = ctx.client.get(f"/api/v1/leads/{lead['id']}/timeline", headers={"X-API-Key": "test-key"}).json()
    types = {i["type"] for i in timeline}
    assert {"sms_received", "reply_handled", "qualification_updated"} <= types


def test_booking_intent_sets_readiness(ctx):
    make_lead(ctx.client)
    r = post_sms(ctx.client, body="Sounds great — can we book a call tomorrow?")
    assert r.status_code == 200
    lead = ctx.client.get("/api/v1/leads", headers={"X-API-Key": "test-key"}).json()[0]
    assert lead["readiness"] == "meeting_requested"


def test_hot_lead_escalates_to_team(tmp_path):
    """Score crosses the HOT threshold -> immediate Slack alert with summary."""
    from tests.conftest import API_KEY, capture_transport
    from app.core.config import Settings
    from app.main import create_app
    from fastapi.testclient import TestClient as TC

    sent_slack, sent_twilio, sent_email = [], [], []
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/hot.db", api_key=API_KEY, environment="test",
        llm_provider="mock", crm_provider="none",
        alert_channels="slack", slack_webhook_url="http://slack.test/hook",
        twilio_account_sid="AC1", twilio_sms_from="+15550001111",
        sendgrid_api_key="SG1",
    )
    transports = {
        "twilio": capture_transport(sent_twilio, status=201, body={"sid": "SM1"}),
        "sendgrid": capture_transport(sent_email, status=202),
        "slack": capture_transport(sent_slack, status=200, body={"ok": True}),
    }
    app = create_app(settings, transports)
    with TC(app) as client:
        r = client.post(
            "/api/v1/leads",
            json={"full_name": "Big Spender", "email": "big@example.com",
                  "phone": "+15551239999", "state": "NY",
                  "consent_sms": True, "consent_email": True, "consent_call": True},
            headers={"X-API-Key": API_KEY},
        )
        assert r.status_code == 201

        r = client.post(
            "/api/v1/webhooks/twilio/sms",
            data={"From": "+15551239999", "To": "+15550001111",
                  "Body": "I have $300k ready and want to close in 2 months — can we book a call?",
                  "MessageSid": "SM2"},
        )
        assert r.status_code == 200

        lead = client.get("/api/v1/leads", headers={"X-API-Key": API_KEY}).json()[0]
        assert lead["tier"] == "hot"
        assert lead["score"] >= 70

    # 2 slack alerts: new-lead alert at intake + the hot escalation
    assert len(sent_slack) == 2
    assert b"HOT" in sent_slack[-1].read().upper()


def test_stop_opts_out_everywhere(ctx):
    make_lead(ctx.client)
    r = post_sms(ctx.client, body="STOP")
    assert r.json()["action"] == "opted_out"

    lead = ctx.client.get("/api/v1/leads", headers={"X-API-Key": "test-key"}).json()[0]
    assert lead["opt_out"] is True

    session = ctx.container.sessionmaker()
    assert session.query(OptOut).count() == 1
    session.close()

    # cadence run sends nothing more
    run = ctx.client.post("/api/v1/cadence/run", headers={"X-API-Key": "test-key"})
    assert run.json()["summary"]["followups_sent"] == 0


def test_stop_confirmation_sent_even_opted_out(ctx):
    make_lead(ctx.client)
    post_sms(ctx.client, body="unsubscribe")
    # confirmation message stored on the lead
    session = ctx.container.sessionmaker()
    msgs = session.query(Message).filter(Message.direction == "outbound").all()
    assert any("unsubscribed" in m.body.lower() for m in msgs)
    session.close()


def test_help_gets_information_reply(ctx):
    make_lead(ctx.client)
    r = post_sms(ctx.client, body="HELP")
    assert r.json()["action"] == "helped"
    session = ctx.container.sessionmaker()
    msgs = session.query(Message).filter(Message.direction == "outbound").all()
    assert any("happy to help" in m.body.lower() or "help" in m.body.lower() for m in msgs)
    session.close()


def test_unknown_phone_is_logged_not_crashing(ctx):
    r = post_sms(ctx.client, from_phone="+19998887777", body="hello?")
    assert r.status_code == 200 and r.json()["matched"] is False


def test_twilio_signature_enforced_when_configured(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/sig.db", api_key="test-key", environment="test",
        twilio_account_sid="AC1", twilio_auth_token="tok123",
        twilio_validate_signatures=True, llm_provider="mock",
        crm_provider="none", alert_channels="",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        from app.models import Lead  # noqa: F401

        url = "http://testserver/api/v1/webhooks/twilio/sms"
        params = {"From": "+15550000001", "To": "+15550001111", "Body": "yo", "MessageSid": "SM1"}
        sig = twilio_signature("tok123", url, params)
        ok = client.post(url, data=params, headers={"X-Twilio-Signature": sig})
        assert ok.status_code == 200  # unmatched phone -> ok/matched False
        bad = client.post(url, data=params, headers={"X-Twilio-Signature": "badsig"})
        assert bad.status_code == 403

"""Test infrastructure: app factory with capturing MockTransports + time/timezone helpers."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.timeutils import utcnow
from app.main import create_app

API_KEY = "test-key"


def capture_transport(captured: list, status: int = 200, body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if body is not None:
            payload = body if isinstance(body, (dict, list)) else json.loads(body)
            return httpx.Response(status, json=payload)
        return httpx.Response(status)

    return httpx.MockTransport(handler)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path}/leadharbor-test.db",
        api_key=API_KEY,
        environment="test",
        llm_provider="mock",
        crm_provider="none",
        alert_channels="",
        twilio_account_sid="AC_test",
        twilio_auth_token="test-twilio-token",
        twilio_validate_signatures=False,  # enabled + tested explicitly in test_webhook_twilio
        twilio_sms_from="+15550001111",
        sendgrid_api_key="SG_test",
        slack_webhook_url="",
        company_name="Meridian Realty Dubai",
        advisor_name="Maya",
        booking_link="https://cal.com/leadharbor/intro",
        facebook_verify_token="test-verify-token",
    )


@pytest.fixture
def ctx(settings):
    """App + client with all outbound HTTP captured for assertions."""
    sent = SimpleNamespace(
        twilio=[], sendgrid=[], slack=[], crm=[], llm=[], graph=[],
    )
    transports = {
        "twilio": capture_transport(sent.twilio, status=201, body={"sid": "SM_TEST_1"}),
        "sendgrid": capture_transport(sent.sendgrid, status=202),
        "slack": capture_transport(sent.slack, status=200, body={"ok": True}),
        "crm": capture_transport(sent.crm, status=200, body={"contact": {"id": "ghl_1"}}),
        "llm": capture_transport(sent.llm, status=500, body={"error": "boom"}),
        "graph": capture_transport(sent.graph, status=200, body={"field_data": []}),
    }
    app = create_app(settings, transports)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, app=app, settings=settings, sent=sent,
            container=app.state.container,
        )


def auth_headers(key: str = API_KEY) -> dict:
    return {"X-API-Key": key}


def make_lead(client, **overrides) -> dict:
    payload = {
        "full_name": "John Carter",
        "email": "john@example.com",
        "phone": "+15551230000",
        "state": "TX",
        "city": "Austin",
        "source": "facebook",
        "campaign": "dubai_investors",
        "ad_name": "Golden Visa US",
        "consent_sms": True,
        "consent_email": True,
        "consent_call": True,
    }
    payload.update(overrides)
    resp = client.post("/api/v1/leads", json=payload, headers=auth_headers())
    assert resp.status_code == 201, resp.text
    return resp.json()


def tz_for_local_hour(hour: int) -> str:
    """An IANA zone whose local hour is `hour` right now (for quiet-hours tests)."""
    utc_hour = utcnow().hour
    d = (hour - utc_hour) % 24
    return f"Etc/GMT-{d}" if d <= 14 else f"Etc/GMT+{24 - d}"

"""LLM integration: OpenAI-compatible path with graceful deterministic fallback."""
from __future__ import annotations

from app.core.events import LLM_ERROR
from app.models import Event, Lead
from app.core.timeutils import utcnow
from tests.conftest import API_KEY, capture_transport
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def llm_settings(tmp_path, provider="openai-compatible") -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path}/llm.db", api_key=API_KEY, environment="test",
        llm_provider=provider, llm_api_key="sk-test", crm_provider="none",
        alert_channels="", twilio_account_sid="AC", twilio_sms_from="+15550001111",
        sendgrid_api_key="SG",
    )


GOOD_COMPLETION = {
    "choices": [
        {
            "message": {
                "content": '{"reply": "Great, Dana! For a $250k budget I would shortlist JVC. '
                           'Shall we book a call this week?", "intent": "answer", "escalate": false, '
                           '"budget_usd": 250000, "timeline_months": 3, "readiness": "general"}'
            }
        }
    ]
}


def make_db_lead(container) -> Lead:
    session = container.sessionmaker()
    lead = Lead(full_name="Dana LLM", email="dana@x.com", phone="+15551115555",
                created_at=utcnow(), updated_at=utcnow(), consent_sms=True, consent_email=True)
    session.add(lead)
    session.commit()
    session.refresh(lead)
    lead_id = lead.id
    session.close()
    session = container.sessionmaker()
    return session.get(Lead, lead_id)


def test_llm_reply_used_when_available(tmp_path):
    sent = []
    app = create_app(
        llm_settings(tmp_path),
        {"llm": capture_transport(sent, status=200, body=GOOD_COMPLETION)},
    )
    with TestClient(app) as client:
        container = app.state.container
        session = container.sessionmaker()
        lead = make_db_lead(container)
        reply = container.agent.compose_reply(session, lead, "I have around $250k")
        session.close()
        assert reply.used_llm is True
        assert "JVC" in reply.text
        assert reply.extracted["budget_usd"] == 250000
        # request shape: OpenAI-compatible chat completion with JSON mode
        req = sent[0]
        assert req.url.path == "/v1/chat/completions"
        body = req.read().decode()
        assert '"response_format"' in body and '"gpt-4o-mini"' in body


def test_llm_failure_falls_back_deterministically(tmp_path):
    app = create_app(llm_settings(tmp_path), {"llm": capture_transport([], status=500, body={"e": 1})})
    with TestClient(app) as client:
        container = app.state.container
        session = container.sessionmaker()
        lead = make_db_lead(container)
        reply = container.agent.compose_reply(session, lead, "I want to talk to a human")
        session.commit()
        session.close()
        assert reply.used_llm is False
        assert "advisor" in reply.text.lower() or "reach out" in reply.text.lower()
        # LLM error event recorded for observability
        session = container.sessionmaker()
        assert session.query(Event).filter(Event.type == LLM_ERROR).count() == 1
        session.close()


def test_mock_provider_skips_llm_entirely(tmp_path):
    app = create_app(llm_settings(tmp_path, provider="mock"))
    with TestClient(app) as client:
        container = app.state.container
        session = container.sessionmaker()
        lead = make_db_lead(container)
        reply = container.agent.compose_reply(session, lead, "what is the golden visa?")
        session.close()
        assert reply.used_llm is False
        assert "10-year" in reply.text or "Golden Visa" in reply.text

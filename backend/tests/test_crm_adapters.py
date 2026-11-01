"""CRM adapters (GoHighLevel + HubSpot) against mocked HTTP — client answer #1: both."""
from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.core.events import CRM_SYNCED
from app.models import Event, Lead
from app.services.crm import NullCRM, get_crm, sync_lead
from app.services.crm.ghl import GoHighLevelAdapter
from app.services.crm.hubspot import HubSpotAdapter
from app.core.timeutils import utcnow


def make_lead_obj(**kw) -> Lead:
    base = dict(
        id=1, full_name="Dana White", email="dana@example.com", phone="+15551234567",
        city="Miami", state="FL", country="US", status="qualified", score=80, tier="hot",
        source="facebook", updated_at=utcnow(),
    )
    base.update(kw)
    return Lead(**base)


class RecordingTransport(httpx.BaseTransport):
    def __init__(self, responses):
        self.requests: list[httpx.Request] = []
        self.responses = list(responses)

    def handle_request(self, request):
        self.requests.append(request)
        resp = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        status, body = resp
        return httpx.Response(status, json=body)


def test_ghl_upsert_stage_note():
    t = RecordingTransport([(200, {"contact": {"id": "ghl_123"}}),
                            (200, {"ok": True}), (200, {"id": "note_1"})])
    adapter = GoHighLevelAdapter("tok", "LOC_1", transport=t)
    lead = make_lead_obj()
    assert adapter.upsert_lead(lead) == "ghl_123"
    lead.crm_id = "ghl_123"  # sync_lead sets this in production

    upsert = t.requests[0]
    assert upsert.url.path == "/contacts/upsert"
    assert b"LOC_1" in upsert.read() or "LOC_1" in (upsert.content or b"").decode(errors="ignore")
    assert upsert.headers["Version"] == "2021-07-28"

    adapter.update_stage(lead)  # tags
    assert "/contacts/ghl_123/tags" == t.requests[1].url.path
    adapter.log_note(lead, "hot lead")
    assert "/contacts/ghl_123/notes" == t.requests[2].url.path


def test_ghl_retries_on_5xx():
    t = RecordingTransport([(500, {"error": "boom"}), (500, {"error": "boom"}), (200, {"contact": {"id": "ok"}})])
    adapter = GoHighLevelAdapter("tok", "LOC_1", transport=t)
    assert adapter.upsert_lead(make_lead_obj()) == "ok"
    assert len(t.requests) == 3


def test_hubspot_creates_when_not_found():
    t = RecordingTransport([(200, {"total": 0, "results": []}), (201, {"id": "hs_9"})])
    adapter = HubSpotAdapter("tok", transport=t)
    assert adapter.upsert_lead(make_lead_obj()) == "hs_9"
    search = t.requests[0]
    assert search.url.path == "/crm/v3/objects/contacts/search"
    assert b"dana@example.com" in search.read()
    create = t.requests[1]
    assert create.url.path == "/crm/v3/objects/contacts"
    payload = create.read().decode()
    assert "leadharbor_status" in payload  # custom properties included by default


def test_hubspot_updates_existing_contact():
    t = RecordingTransport([(200, {"total": 1, "results": [{"id": "hs_7"}]}), (200, {"id": "hs_7"})])
    adapter = HubSpotAdapter("tok", transport=t)
    assert adapter.upsert_lead(make_lead_obj()) == "hs_7"
    assert t.requests[1].method == "PATCH"


def test_hubspot_custom_props_disabled():
    t = RecordingTransport([(200, {"total": 0, "results": []}), (201, {"id": "hs_2"})])
    adapter = HubSpotAdapter("tok", transport=t, use_custom_properties=False)
    adapter.upsert_lead(make_lead_obj())
    assert "leadharbor_status" not in t.requests[1].read().decode()


def test_sync_lead_null_crm_sets_local_id():
    import sqlalchemy

    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    from app.core.db import Base

    Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session as S

    with S(engine) as session:
        lead = Lead(full_name="Local Lead", email="l@x.com", created_at=utcnow(), updated_at=utcnow())
        session.add(lead)
        session.flush()
        sync_lead(session, NullCRM(), lead)
        assert lead.crm_id == f"local-{lead.id}"
        assert lead.crm_provider == "none"
        assert session.query(Event).filter(Event.type == CRM_SYNCED).count() == 1


def test_sync_failure_never_raises():
    import sqlalchemy

    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    from app.core.db import Base

    Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session as S

    class ExplodingCRM(NullCRM):
        name = "explode"

        def upsert_lead(self, lead):
            raise RuntimeError("api down")

    with S(engine) as session:
        lead = Lead(full_name="X", created_at=utcnow(), updated_at=utcnow())
        session.add(lead)
        session.flush()
        sync_lead(session, ExplodingCRM(), lead)  # must not raise
        events = session.query(Event).all()
        assert any(e.type == "crm_sync_failed" for e in events)


def test_factory_falls_back_to_null_without_credentials():
    assert get_crm(Settings(crm_provider="ghl", ghl_access_token="")).name == "none"
    assert get_crm(Settings(crm_provider="hubspot", hubspot_access_token="tok")).name == "hubspot"
    assert get_crm(Settings(crm_provider="none")).name == "none"

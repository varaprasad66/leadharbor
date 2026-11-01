"""GoHighLevel (LeadConnector) API v2 adapter — client question #1 path A."""
from __future__ import annotations

import httpx

from app.models import Lead
from app.services.crm.base import CRMAdapter
from app.services.http_client import request_with_retries

BASE_URL = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"


class GoHighLevelAdapter(CRMAdapter):
    name = "ghl"

    def __init__(self, access_token: str, location_id: str, transport: httpx.BaseTransport | None = None):
        super().__init__(transport)
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Version": API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._location_id = location_id

    def upsert_lead(self, lead: Lead) -> str | None:
        with self._client(BASE_URL, self._headers) as client:
            resp = request_with_retries(
                client,
                "POST",
                "/contacts/upsert",
                json={
                    "locationId": self._location_id,
                    "firstName": (lead.full_name or "").split(" ")[0],
                    "lastName": " ".join((lead.full_name or "").split(" ")[1:]),
                    "email": lead.email,
                    "phone": lead.phone,
                    "city": lead.city,
                    "state": lead.state,
                    "country": lead.country,
                    "tags": [f"leadharbor:{lead.status}", f"tier:{lead.tier}", f"source:{lead.source}"],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            contact = data.get("contact") or data
            return contact.get("id")

    def update_stage(self, lead: Lead) -> None:
        if not lead.crm_id:
            return
        with self._client(BASE_URL, self._headers) as client:
            resp = request_with_retries(
                client,
                "POST",
                f"/contacts/{lead.crm_id}/tags",
                json={"tags": [f"leadharbor:{lead.status}", f"tier:{lead.tier}"]},
            )
            resp.raise_for_status()

    def log_note(self, lead: Lead, text: str) -> None:
        if not lead.crm_id:
            return
        with self._client(BASE_URL, self._headers) as client:
            resp = request_with_retries(
                client, "POST", f"/contacts/{lead.crm_id}/notes", json={"body": text[:5000]}
            )
            resp.raise_for_status()

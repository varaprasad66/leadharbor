"""HubSpot CRM v3 adapter — client question #1 path B.

Custom properties used (create them once in HubSpot settings, see
docs/CRM_INTEGRATION.md): leadharbor_status, leadharbor_tier, leadharbor_score.
Set LEADHARBOR_HUBSPOT_CUSTOM_PROPS=false to skip them (default props only).
"""
from __future__ import annotations

import httpx

from app.models import Lead
from app.services.crm.base import CRMAdapter
from app.services.http_client import request_with_retries

BASE_URL = "https://api.hubapi.com"
NOTE_TO_CONTACT_ASSOCIATION = 202


class HubSpotAdapter(CRMAdapter):
    name = "hubspot"

    def __init__(
        self,
        access_token: str,
        transport: httpx.BaseTransport | None = None,
        use_custom_properties: bool = True,
    ):
        super().__init__(transport)
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        self._custom_props = use_custom_properties

    def _properties(self, lead: Lead) -> dict:
        parts = (lead.full_name or "").split(" ", 1)
        props = {
            "email": lead.email,
            "firstname": parts[0] if parts else "",
            "lastname": parts[1] if len(parts) > 1 else "",
            "phone": lead.phone,
            "city": lead.city,
            "state": lead.state,
            "country": lead.country,
        }
        if self._custom_props:
            props.update(
                {
                    "leadharbor_status": lead.status,
                    "leadharbor_tier": lead.tier,
                    "leadharbor_score": str(lead.score),
                }
            )
        return {k: v for k, v in props.items() if v is not None}

    def _find_by_email(self, client: httpx.Client, email: str) -> str | None:
        resp = request_with_retries(
            client,
            "POST",
            "/crm/v3/objects/contacts/search",
            json={
                "filterGroups": [
                    {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
                ],
                "properties": ["email"],
                "limit": 1,
            },
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0]["id"] if results else None

    def upsert_lead(self, lead: Lead) -> str | None:
        with self._client(BASE_URL, self._headers) as client:
            existing = None
            if lead.email:
                existing = self._find_by_email(client, lead.email)
            if existing:
                resp = request_with_retries(
                    client, "PATCH", f"/crm/v3/objects/contacts/{existing}",
                    json={"properties": self._properties(lead)},
                )
                resp.raise_for_status()
                return existing
            resp = request_with_retries(
                client, "POST", "/crm/v3/objects/contacts",
                json={"properties": self._properties(lead)},
            )
            resp.raise_for_status()
            return resp.json().get("id")

    def update_stage(self, lead: Lead) -> None:
        if not lead.crm_id:
            return
        props = {"leadharbor_status": lead.status, "leadharbor_tier": lead.tier} if self._custom_props else None
        if not props:
            return
        with self._client(BASE_URL, self._headers) as client:
            resp = request_with_retries(
                client, "PATCH", f"/crm/v3/objects/contacts/{lead.crm_id}",
                json={"properties": props},
            )
            resp.raise_for_status()

    def log_note(self, lead: Lead, text: str) -> None:
        if not lead.crm_id:
            return
        with self._client(BASE_URL, self._headers) as client:
            resp = request_with_retries(
                client,
                "POST",
                "/crm/v3/objects/notes",
                json={
                    "properties": {
                        "hs_note_body": text[:5000],
                        "hs_timestamp": str(int(lead.updated_at.timestamp() * 1000)),
                    },
                    "associations": [
                        {
                            "to": {"id": lead.crm_id},
                            "types": [
                                {
                                    "associationCategory": "HUBSPOT_DEFINED",
                                    "associationTypeId": NOTE_TO_CONTACT_ASSOCIATION,
                                }
                            ],
                        }
                    ],
                },
            )
            resp.raise_for_status()

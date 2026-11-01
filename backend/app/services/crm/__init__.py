"""CRM factory + sync wrapper. CRM failures are logged as events, never break the funnel."""
from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.events import CRM_SYNCED, CRM_SYNC_FAILED, log_event
from app.core.logging import get_logger
from app.models import Lead
from app.services.crm.base import CRMAdapter
from app.services.crm.ghl import GoHighLevelAdapter
from app.services.crm.hubspot import HubSpotAdapter

log = get_logger("leadharbor.crm")


class NullCRM(CRMAdapter):
    name = "none"

    def upsert_lead(self, lead: Lead) -> str | None:
        return f"local-{lead.id}"

    def update_stage(self, lead: Lead) -> None:
        return None

    def log_note(self, lead: Lead, text: str) -> None:
        return None


def get_crm(settings: Settings, transport: httpx.BaseTransport | None = None) -> CRMAdapter:
    if settings.crm_provider == "ghl" and settings.ghl_access_token:
        return GoHighLevelAdapter(settings.ghl_access_token, settings.ghl_location_id, transport)
    if settings.crm_provider == "hubspot" and settings.hubspot_access_token:
        return HubSpotAdapter(
            settings.hubspot_access_token, transport,
            use_custom_properties=settings.hubspot_custom_properties,
        )
    return NullCRM()


def sync_lead(
    session: Session,
    crm: CRMAdapter,
    lead: Lead,
    *,
    note: str | None = None,
    force_upsert: bool = False,
) -> None:
    """Upsert (first time) + stage update + optional note. Best-effort by design."""
    try:
        if force_upsert or not lead.crm_id or lead.crm_provider != crm.name:
            crm_id = crm.upsert_lead(lead)
            lead.crm_id = crm_id
            lead.crm_provider = crm.name
        crm.update_stage(lead)
        if note:
            crm.log_note(lead, note)
        log_event(session, CRM_SYNCED, lead.id, {"crm": crm.name, "crm_id": lead.crm_id})
    except Exception as exc:  # noqa: BLE001 - CRM must never break lead flow
        log.warning("crm sync failed for lead %s: %s", lead.id, exc)
        log_event(session, CRM_SYNC_FAILED, lead.id, {"crm": crm.name, "error": str(exc)[:300]})

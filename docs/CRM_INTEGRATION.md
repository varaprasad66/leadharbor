# LeadHarbor — CRM Integration (GoHighLevel **and** HubSpot)

The client asked for compatibility with both platforms. LeadHarbor implements a 3-method CRM
contract (`services/crm/base.py`) with two production adapters, switched by a single env var.
CRM failures are logged as `crm_synced` / `crm_sync_failed` events and never block the funnel.

```
LEADHARBOR_CRM_PROVIDER=ghl | hubspot | none
```

## What gets synced

| Moment | Action |
|---|---|
| Lead created | Upsert contact (match by email; create when missing) |
| Any status/tier change | Stage update + tier tag/property |
| HOT escalation, booking, no-show | Note on the contact with a human-readable summary |

Synced fields: name, email, phone (E.164), city, state, country, status, tier, score, source.

---

## Path A — GoHighLevel (LeadConnector API v2)

Requires: `LEADHARBOR_CRM_PROVIDER=ghl`, `LEADHARBOR_GHL_ACCESS_TOKEN`,
`LEADHARBOR_GHL_LOCATION_ID`.

1. Sub-account → Settings → Private Integrations → create token with scopes
   `contacts.readonly contacts.write`.
2. Copy the token + the sub-account's Location ID.

Endpoints used: `POST /contacts/upsert`, `POST /contacts/{id}/tags`, `POST /contacts/{id}/notes`
(header `Version: 2021-07-28`). Stages surface as tags `leadharbor:{status}` + `tier:{tier}` —
build pipeline views from tags, or add a GHL workflow triggered on the tag.

## Path B — HubSpot (CRM v3)

Requires: `LEADHARBOR_CRM_PROVIDER=hubspot`, `LEADHARBOR_HUBSPOT_ACCESS_TOKEN`.

1. HubSpot → Settings → Integrations → Private Apps → create with scopes
   `crm.objects.contacts.write`, `crm.objects.contacts.read`, `crm.objects.notes.write`,
   `sse.contacts` (search).
2. Copy the token.

Endpoints used: `POST /crm/v3/objects/contacts/search` (email match), `POST/PATCH
/crm/v3/objects/contacts`, `POST /crm/v3/objects/notes` (associated to the contact,
association type 202).

**One-time setup — create these custom contact properties** (Settings → Properties):
`leadharbor_status` (single-line text), `leadharbor_tier` (single-line text),
`leadharbor_score` (number). If you skip them, set `LEADHARBOR_HUBSPOT_CUSTOM_PROPERTIES=false`
— sync then uses default properties only.

## Path C — none (local dev / CRM-free mode)

`LEADHARBOR_CRM_PROVIDER=none` → contacts get `crm_id = local-{id}`; everything else works.

## Adding a third CRM

Implement in `backend/app/services/crm/`:

```python
class MyCRM(CRMAdapter):
    name = "mycrm"
    def upsert_lead(self, lead: Lead) -> str | None: ...
    def update_stage(self, lead: Lead) -> None: ...
    def log_note(self, lead: Lead, text: str) -> None: ...
```

…register it in `services/crm/__init__.py::get_crm`, and add contract tests mirroring
`tests/test_crm_adapters.py`.

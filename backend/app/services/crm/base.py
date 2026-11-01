"""CRM adapter contract. Implementations never touch the DB; sync_lead owns that."""
from __future__ import annotations

import httpx

from app.models import Lead


class CRMAdapter:
    name: str = "base"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._transport = transport

    def _client(self, base_url: str, headers: dict) -> httpx.Client:
        from app.services.http_client import make_client

        return make_client(transport=self._transport, base_url=base_url, headers=headers)

    # --- contract ---
    def upsert_lead(self, lead: Lead) -> str | None:  # pragma: no cover - interface
        raise NotImplementedError

    def update_stage(self, lead: Lead) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def log_note(self, lead: Lead, text: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

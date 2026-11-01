"""Shared HTTP client factory with retry/backoff. Tests inject httpx.MockTransport."""
from __future__ import annotations

import time

import httpx

from app.core.logging import get_logger

log = get_logger("leadharbor.http")


def make_client(
    transport: httpx.BaseTransport | None = None,
    base_url: str | None = None,
    headers: dict | None = None,
    timeout: float = 15.0,
) -> httpx.Client:
    return httpx.Client(
        transport=transport,
        base_url=base_url,
        headers=headers or {},
        timeout=timeout,
    )


def request_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    retries: int = 3,
    backoff_seconds: float = 0.2,
    **kwargs,
) -> httpx.Response:
    """POST/GET with retry on 5xx and network errors. Raises on final failure."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.request(method, url, **kwargs)
            if resp.status_code < 500:
                return resp
            last_exc = httpx.HTTPStatusError(
                f"upstream {resp.status_code}", request=resp.request, response=resp
            )
        except httpx.HTTPError as exc:  # network-level
            last_exc = exc
        if attempt < retries - 1:
            time.sleep(backoff_seconds * (2**attempt))
    assert last_exc is not None
    raise last_exc

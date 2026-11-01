"""API-key auth + Twilio webhook signature verification (HMAC-SHA1, Twilio spec)."""
from __future__ import annotations

import base64
import hashlib
import hmac

from fastapi import HTTPException, Request

from app.core.config import Settings


def require_api_key(request: Request, x_api_key: str | None) -> None:
    expected = request.app.state.container.settings.api_key
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    data = url + "".join(k + v for k, v in sorted(params.items()))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_twilio_signature(request: Request, params: dict[str, str], settings: Settings) -> None:
    """Enforce HMAC verification in production; disable via LEADHARBOR_TWILIO_VALIDATE_SIGNATURES=false."""
    if not settings.twilio_validate_signatures or not settings.twilio_auth_token:
        return
    signature = request.headers.get("X-Twilio-Signature", "")
    expected = twilio_signature(settings.twilio_auth_token, str(request.url), params)
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="invalid Twilio signature")

"""Phone normalization to E.164 (US-centric default per the client's market)."""
from __future__ import annotations

import re

DIGITS_RE = re.compile(r"\D+")


class InvalidPhone(Exception):
    pass


def normalize_phone(raw: str, default_country_code: str = "1") -> str:
    """Normalize to E.164. Handles +, US leading 1, punctuation, extensions stripped."""
    if not raw or not raw.strip():
        raise InvalidPhone("empty phone")
    cleaned = DIGITS_RE.sub("", raw.strip())
    if not cleaned:
        raise InvalidPhone(f"no digits in {raw!r}")
    if raw.strip().startswith("+"):
        if not 8 <= len(cleaned) <= 15:
            raise InvalidPhone(f"invalid length for {raw!r}")
        return f"+{cleaned}"
    if len(cleaned) == 10:
        return f"+{default_country_code}{cleaned}"
    if len(cleaned) == 11 and cleaned.startswith(default_country_code):
        return f"+{cleaned}"
    if 8 <= len(cleaned) <= 15:
        return f"+{cleaned}"
    raise InvalidPhone(f"cannot normalize {raw!r}")


def last10(phone: str) -> str:
    """Last 10 digits, for matching leads whose number arrives with/without +1."""
    return DIGITS_RE.sub("", phone)[-10:]

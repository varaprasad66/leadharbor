"""US state -> IANA timezone. Whole-state approximation (documented in ASSUMPTIONS.md)."""
from __future__ import annotations

EASTERN = "America/New_York"
CENTRAL = "America/Chicago"
MOUNTAIN = "America/Denver"
ARIZONA = "America/Phoenix"
PACIFIC = "America/Los_Angeles"
ALASKA = "America/Anchorage"
HAWAII = "Pacific/Honolulu"

STATE_TZ: dict[str, str] = {
    **{s: EASTERN for s in (
        "CT DE DC GA IN KY ME MD MA MI NH NJ NY NC OH PA RI SC VT VA WV FL".split()
    )},
    **{s: CENTRAL for s in (
        "AL AR IL IA KS LA MN MS MO NE ND OK SD TN TX WI".split()
    )},
    **{s: MOUNTAIN for s in ("CO ID MT NM UT WY".split())},
    "AZ": ARIZONA,
    **{s: PACIFIC for s in ("CA NV OR WA".split())},
    "AK": ALASKA,
    "HI": HAWAII,
}

DEFAULT_TZ = EASTERN


def state_to_timezone(state: str | None) -> str:
    if not state:
        return DEFAULT_TZ
    return STATE_TZ.get(state.strip().upper()[:2], DEFAULT_TZ)

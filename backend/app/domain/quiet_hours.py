"""Timezone-aware quiet hours (TCPA: no marketing SMS/calls 8am-9pm rule inverted -> send window).

Quiet window: [start_hour, end_hour) in the LEAD's local time (client question #5:
hours are configurable; defaults 21 -> 8).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.core.timeutils import as_utc, from_local, to_local


def is_quiet_hours(
    now_utc: datetime,
    tz_name: str,
    start_hour: int = 21,
    end_hour: int = 8,
) -> bool:
    local = to_local(now_utc, tz_name)
    if start_hour == end_hour:
        return False
    if start_hour < end_hour:  # e.g. quiet 02:00-08:00
        return start_hour <= local.hour < end_hour
    # wrap past midnight, e.g. 21 -> 8
    return local.hour >= start_hour or local.hour < end_hour


def next_send_window(
    now_utc: datetime,
    tz_name: str,
    start_hour: int = 21,
    end_hour: int = 8,
) -> datetime:
    """Return the next naive-UTC datetime when sending is allowed."""
    if not is_quiet_hours(now_utc, tz_name, start_hour, end_hour):
        return now_utc
    local = to_local(now_utc, tz_name)
    candidate = local.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return from_local(candidate, tz_name)


def defer_until(now_utc: datetime, tz_name: str, start_hour: int, end_hour: int) -> datetime:
    """Alias with explicit semantics for the messaging guard."""
    return next_send_window(as_utc(now_utc).replace(tzinfo=None), tz_name, start_hour, end_hour)

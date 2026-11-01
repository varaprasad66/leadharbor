"""Time helpers. Convention: all DB datetimes are NAIVE UTC. Convert at the edges."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    """Naive UTC 'now' (the DB convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(dt: datetime) -> datetime:
    """Interpret a naive datetime as UTC and return it timezone-aware."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_local(dt_utc: datetime, tz_name: str) -> datetime:
    """Convert a naive-UTC datetime to an aware local datetime in tz_name."""
    return as_utc(dt_utc).astimezone(ZoneInfo(tz_name))


def from_local(local_dt: datetime, tz_name: str) -> datetime:
    """Convert an aware local datetime back to naive UTC for storage."""
    return local_dt.astimezone(timezone.utc).replace(tzinfo=None)

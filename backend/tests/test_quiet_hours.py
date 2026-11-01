"""TCPA quiet-hours math across timezones and DST."""
from __future__ import annotations

from datetime import datetime

from app.domain.quiet_hours import is_quiet_hours, next_send_window


def test_daytime_is_allowed_new_york():
    assert not is_quiet_hours(datetime(2026, 1, 15, 15, 0), "America/New_York")  # 10:00 EST


def test_night_is_quiet_new_york():
    assert is_quiet_hours(datetime(2026, 1, 15, 3, 0), "America/New_York")  # 22:00 Jan 14 EST


def test_next_send_window_is_8am_local():
    # 22:00 EST -> next window 08:00 EST = 13:00 UTC
    assert next_send_window(datetime(2026, 1, 15, 3, 0), "America/New_York") == datetime(2026, 1, 15, 13, 0)


def test_la_7am_is_quiet():
    # 15:00 UTC = 07:00 PST -> quiet, next window 08:00 PST = 16:00 UTC
    assert is_quiet_hours(datetime(2026, 1, 15, 15, 0), "America/Los_Angeles")
    assert next_send_window(datetime(2026, 1, 15, 15, 0), "America/Los_Angeles") == datetime(2026, 1, 15, 16, 0)


def test_respects_dst_summer_offset():
    # EDT = UTC-4: 03:00 UTC = 23:00 (quiet); 8am EDT = 12:00 UTC
    assert is_quiet_hours(datetime(2026, 7, 15, 3, 0), "America/New_York")
    assert next_send_window(datetime(2026, 7, 15, 3, 0), "America/New_York") == datetime(2026, 7, 15, 12, 0)


def test_custom_window_start_before_end():
    # quiet 13:00-14:00 local
    assert is_quiet_hours(datetime(2026, 1, 15, 18, 30), "America/New_York", 13, 14)  # 13:30 EST
    assert not is_quiet_hours(datetime(2026, 1, 15, 19, 30), "America/New_York", 13, 14)  # 14:30 EST


def test_equal_hours_disable_quiet_hours():
    assert not is_quiet_hours(datetime(2026, 1, 15, 3, 0), "America/New_York", 8, 8)


def test_midnight_boundary():
    # 04:00 UTC = 23:00 previous day in LA (quiet) ; 11:59 UTC = 03:59 LA (quiet)
    assert is_quiet_hours(datetime(2026, 1, 15, 12, 0), "America/Los_Angeles")
    assert not is_quiet_hours(datetime(2026, 1, 15, 17, 0), "America/Los_Angeles")  # 09:00 PST


def test_dubai_timezone():
    # Dubai = UTC+4. 18:00 UTC = 22:00 -> quiet
    assert is_quiet_hours(datetime(2026, 1, 15, 18, 0), "Asia/Dubai")
    assert next_send_window(datetime(2026, 1, 15, 18, 0), "Asia/Dubai") == datetime(2026, 1, 16, 4, 0)

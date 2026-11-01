"""Phone normalization + US state -> timezone enrichment."""
from __future__ import annotations

import pytest

from app.domain.phone import InvalidPhone, last10, normalize_phone
from app.domain.us_timezone import state_to_timezone


def test_us_10_digit_gets_country_code():
    assert normalize_phone("(512) 555-1234") == "+15125551234"
    assert normalize_phone("512.555.1234") == "+15125551234"
    assert normalize_phone("512-555-1234") == "+15125551234"


def test_leading_one_stripped_correctly():
    assert normalize_phone("1 512 555 1234") == "+15125551234"


def test_e164_passthrough():
    assert normalize_phone("+15551234567") == "+15551234567"
    assert normalize_phone("+971501234567") == "+971501234567"


def test_invalid_phones_raise():
    for bad in ("", "   ", "123", "abcdefghij", "+123"):
        with pytest.raises(InvalidPhone):
            normalize_phone(bad)


def test_last10_matching():
    assert last10("+15125551234") == last10("5125551234") == "5125551234"


def test_state_to_timezone():
    assert state_to_timezone("CA") == "America/Los_Angeles"
    assert state_to_timezone("ca") == "America/Los_Angeles"
    assert state_to_timezone("TX") == "America/Chicago"
    assert state_to_timezone("NY") == "America/New_York"
    assert state_to_timezone("AZ") == "America/Phoenix"
    assert state_to_timezone("AK") == "America/Anchorage"
    assert state_to_timezone("HI") == "Pacific/Honolulu"


def test_unknown_or_missing_state_defaults_eastern():
    assert state_to_timezone(None) == "America/New_York"
    assert state_to_timezone("XX") == "America/New_York"

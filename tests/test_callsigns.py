"""Tests for registration.callsigns helpers."""
from __future__ import annotations

import pytest

from registration.callsigns import (
    core_callsign,
    is_valid_callsign,
    login_username,
    normalize_callsign,
)


@pytest.mark.parametrize("raw,expected", [
    ("hb9tvk", "HB9TVK"),
    ("  HB9TVK/P  ", "HB9TVK/P"),
    ("", ""),
    (None, ""),
])
def test_normalize_callsign(raw, expected):
    assert normalize_callsign(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("HB9TVK", True),
    ("HB9TVK/P", True),
    ("OE/HB9TVK/P", True),
    ("hb9tvk", True),  # case-insensitive
    ("", False),
    ("HB9 TVK", False),  # space not allowed
])
def test_is_valid_callsign(raw, expected):
    assert is_valid_callsign(raw) is expected


@pytest.mark.parametrize("raw,expected", [
    ("HB9TVK", "HB9TVK"),
    ("HB9TVK/P", "HB9TVK"),
    ("HB9TVK/MM", "HB9TVK"),
    ("OE/HB9TVK/P", "OE/HB9TVK"),
    # Final segment is not a short letter-only suffix → keep as-is.
    ("HB9XYZ/4", "HB9XYZ/4"),
])
def test_login_username(raw, expected):
    assert login_username(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("HB9TVK", "HB9TVK"),
    ("HB9TVK/P", "HB9TVK"),
    ("hb9tvk/p", "HB9TVK"),
    ("OE/HB9TVK/P", "HB9TVK"),
    ("F/HB9AFI/P", "HB9AFI"),
    ("DL1ABC", "DL1ABC"),
    ("WB9XYZ/4", "WB9XYZ"),
    ("5B/G3ABC", "G3ABC"),
    ("", ""),
])
def test_core_callsign(raw, expected):
    """core_callsign strips both country prefix and portable/area
    postfix to return the home callsign for external lookups."""
    assert core_callsign(raw) == expected

"""M3.1 — text-distance + exchange-normalization helpers."""
from __future__ import annotations

import pytest

from scoring.text_match import (
    DEFAULT_MAX_ERRORS,
    normalize_exchange,
    text_distance,
    texts_match,
)


# --- normalize_exchange ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", ""),
        ("HB9TVK PIZ KESCH 3418M", "HB9TVKPIZKESCH3418M"),
        ("  hb9tvk piz kesch  ", "HB9TVKPIZKESCH"),
        ("hb9tvk\tpiz\tkesch", "HB9TVKPIZKESCH"),
        ("HB9TVK  PIZ   KESCH", "HB9TVKPIZKESCH"),  # multiple internal spaces
        ("hb9tvk/p", "HB9TVK/P"),  # allowed punctuation survives
    ],
)
def test_normalize_exchange(raw, expected):
    assert normalize_exchange(raw) == expected


def test_normalize_exchange_handles_none_like():
    assert normalize_exchange("") == ""


# --- text_distance ---------------------------------------------------------------------------


def test_distance_zero_for_identical_after_normalization():
    assert text_distance("HB9TVK PIZ KESCH", "hb9tvk  piz  kesch") == 0


def test_distance_one_substitution():
    assert text_distance("HB9TVK PIZ KESCH", "HB9TVK PIZ KESEH") == 1


def test_distance_one_deletion():
    assert text_distance("HB9TVK PIZ KESCH", "HB9TVK PIZ KESC") == 1


def test_distance_one_insertion():
    assert text_distance("HB9TVK PIZ KESCH", "HB9TVK PIZX KESCH") == 1


def test_distance_two_errors_in_different_places():
    # substitution at pos 1 + deletion at the end
    assert text_distance("HB9TVK PIZ KESCH", "HX9TVK PIZ KESC") == 2


def test_distance_against_empty():
    assert text_distance("", "HB9TVK") == len("HB9TVK")
    assert text_distance("HB9TVK", "") == len("HB9TVK")
    assert text_distance("", "") == 0


def test_distance_is_symmetric():
    a, b = "HB9TVK PIZ KESCH 3418M", "HB9TVK PIZ KESC 3418M"
    assert text_distance(a, b) == text_distance(b, a)


def test_distance_uses_normalized_form_so_whitespace_is_free():
    # 7 internal spaces removed by normalization shouldn't cost anything.
    assert text_distance("HB9TVK PIZ KESCH", "H B 9 T V K P I Z K E S C H") == 0


# --- texts_match -----------------------------------------------------------------------------


def test_texts_match_default_tolerance_is_two():
    # 2 errors → still a match under the default policy.
    assert texts_match("HB9TVK PIZ KESCH", "HX9TVK PIZ KESC") is True
    # 3 errors → no longer a match.
    assert texts_match("HB9TVK PIZ KESCH", "HX9TXK PIZ KESC") is False


def test_texts_match_with_custom_tolerance():
    assert texts_match("ABCDE", "ABCDX", max_errors=0) is False
    assert texts_match("ABCDE", "ABCDX", max_errors=1) is True


def test_default_max_errors_constant():
    # The 2-error rule is "established practice" — make sure the constant
    # doesn't drift silently.
    assert DEFAULT_MAX_ERRORS == 2

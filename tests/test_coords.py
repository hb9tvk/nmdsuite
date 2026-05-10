"""Coordinate parser + transformer tests."""
from __future__ import annotations

import pytest

from core.models import Participant
from registration.coords import (
    CoordinateError,
    parse_coordinate_pair,
)

def _in_swiss_lv95(e: float, n: float) -> bool:
    return 2_470_000 <= e <= 2_860_000 and 1_065_000 <= n <= 1_310_000


def _in_swiss_wgs84(lat: float, lon: float) -> bool:
    return 5.5 <= lon <= 11.0 and 45.5 <= lat <= 48.0


# --- per-system detection ---------------------------------------------------------------------


def test_parse_wgs84_decimal():
    p = parse_coordinate_pair("8.2546", "46.9789")
    assert p.detected_system == Participant.CoordSystem.WGS84
    assert p.wgs84_lon == pytest.approx(8.2546, abs=1e-4)
    assert p.wgs84_lat == pytest.approx(46.9789, abs=1e-4)
    assert _in_swiss_lv95(p.ch1903p_e, p.ch1903p_n)
    assert p.swapped is False


def test_parse_ch1903plus():
    # 2'661'735 / 1'203'214 — somewhere on Pilatus.
    p = parse_coordinate_pair("2661735", "1203214")
    assert p.detected_system == Participant.CoordSystem.CH1903PLUS
    assert p.ch1903p_e == 2_661_735
    assert p.ch1903p_n == 1_203_214
    assert _in_swiss_wgs84(p.wgs84_lat, p.wgs84_lon)


def test_parse_ch1903_legacy():
    p = parse_coordinate_pair("661735", "203214")
    assert p.detected_system == Participant.CoordSystem.CH1903
    # LV03 → LV95 is offset by exactly +2_000_000 / +1_000_000.
    assert p.ch1903p_e == pytest.approx(2_661_735, abs=2)
    assert p.ch1903p_n == pytest.approx(1_203_214, abs=2)


def test_round_trip_wgs84_through_lv95_is_stable():
    """Parse WGS84 → emit LV95 → parse LV95 → emitted WGS84 should match within ~1 m."""
    first = parse_coordinate_pair("8.2546", "46.9789")
    second = parse_coordinate_pair(str(first.ch1903p_e), str(first.ch1903p_n))
    assert second.wgs84_lat == pytest.approx(first.wgs84_lat, abs=1e-5)
    assert second.wgs84_lon == pytest.approx(first.wgs84_lon, abs=1e-5)


# --- input tolerance --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "e_input,n_input",
    [
        ("2'661'735", "1'203'214"),                    # apostrophes
        (" 2661735 ", " 1203214 "),                     # whitespace
        ("2661735.0", "1203214.0"),                     # decimal points
        ("8,2546", "46,9789"),                          # comma decimal
        ("8.2546 E", "46.9789 N"),                      # direction suffix
    ],
)
def test_parse_tolerates_input_noise(e_input, n_input):
    p = parse_coordinate_pair(e_input, n_input)
    # All these should resolve to the Pilatus area.
    assert p.wgs84_lat == pytest.approx(46.9789, abs=1e-2)
    assert p.wgs84_lon == pytest.approx(8.2546, abs=1e-2)


def test_swapped_e_n_is_recovered():
    # User accidentally swaps the two fields; we should still figure it out.
    p = parse_coordinate_pair("46.9789", "8.2546")
    assert p.detected_system == Participant.CoordSystem.WGS84
    assert p.swapped is True
    assert p.wgs84_lon == pytest.approx(8.2546, abs=1e-4)
    assert p.wgs84_lat == pytest.approx(46.9789, abs=1e-4)


# --- failure modes ----------------------------------------------------------------------------


def test_outside_switzerland_raises():
    # Mt. Everest — definitely not in CH.
    with pytest.raises(CoordinateError):
        parse_coordinate_pair("86.9250", "27.9881")


def test_garbage_raises():
    with pytest.raises(CoordinateError):
        parse_coordinate_pair("nope", "nada")


def test_empty_raises():
    with pytest.raises(CoordinateError):
        parse_coordinate_pair("", "")

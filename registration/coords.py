"""Coordinate parsing and transformation for the Swiss NMD context.

Supports three input systems and converts to canonical CH1903+ (LV95) +
WGS84 representations:

- **WGS84** decimal degrees, e.g. ``8.2275 / 46.8182``.
- **CH1903 (LV03)** — old Swiss grid, 6-digit easting/northing,
  e.g. ``660'000 / 190'000``.
- **CH1903+ (LV95)** — new Swiss grid, 7-digit easting/northing,
  e.g. ``2'660'000 / 1'190'000``.

The system is **inferred** from the numeric magnitude of the inputs; the user
does not have to pick. If the easting/northing fields are entered in
swapped order the parser tolerates the swap when unambiguous.

DMS notation (e.g. ``46°48'05"``) is *not* supported in this slice — the
contest's audience overwhelmingly uses decimal degrees from GPS apps and the
Swiss grid from Swisstopo. Add DMS later if the need shows up.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from pyproj import Transformer

from core.models import Participant

# --- bounding boxes -------------------------------------------------------------------------
#
# Generous Swiss bounds, padded ~10 km past the political border so a
# location near the frontier doesn't fail. CH1903 ranges follow the same
# rule offset by exactly 2_000_000 / 1_000_000 to get to LV95.

_CH1903P_E: Final = (2_470_000, 2_860_000)
_CH1903P_N: Final = (1_065_000, 1_310_000)

_CH1903_E: Final = (470_000, 860_000)
_CH1903_N: Final = (65_000, 310_000)

# WGS84 over Switzerland: lon ~5.8–10.6, lat ~45.7–47.9. Pad slightly.
_WGS84_LON: Final = (5.5, 11.0)
_WGS84_LAT: Final = (45.5, 48.0)


# --- transformers ---------------------------------------------------------------------------
#
# always_xy=True forces (x=longitude/easting, y=latitude/northing) input order
# regardless of CRS axis convention. Saves us from a class of subtle bugs.

_LV95_TO_WGS84 = Transformer.from_crs("EPSG:2056", "EPSG:4326", always_xy=True)
_WGS84_TO_LV95 = Transformer.from_crs("EPSG:4326", "EPSG:2056", always_xy=True)
_LV03_TO_LV95 = Transformer.from_crs("EPSG:21781", "EPSG:2056", always_xy=True)


# --- exceptions -----------------------------------------------------------------------------


class CoordinateError(ValueError):
    """The supplied inputs are not parseable as Swiss coordinates."""


# --- parser ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedCoordinates:
    """Output of :func:`parse_coordinate_pair`. All four canonical fields populated."""

    detected_system: str  # one of Participant.CoordSystem values
    ch1903p_e: float
    ch1903p_n: float
    wgs84_lat: float
    wgs84_lon: float
    swapped: bool  # True if E/N inputs were detected in reverse order


def _to_float(s: str) -> float:
    """Tolerantly parse a numeric string from the form.

    Accepts decimal point or comma, surrounding whitespace, thousand-separator
    apostrophes (``2'660'000``), trailing direction letters (N/S/E/W), and a
    trailing degree sign.
    """
    if s is None:
        raise CoordinateError("empty input")
    raw = s.strip()
    if not raw:
        raise CoordinateError("empty input")
    cleaned = (
        raw.replace("'", "")
        .replace(" ", "")
        .replace(" ", "")
        .replace("°", "")
        .replace(",", ".")
    )
    # Strip a single trailing direction marker; we don't honour it (the form
    # already labels which field is which) but accept it as a courtesy.
    if cleaned and cleaned[-1] in "NnSsEeWw":
        cleaned = cleaned[:-1]
    try:
        return float(cleaned)
    except ValueError as exc:
        raise CoordinateError(f"not a number: {raw!r}") from exc


def _in_range(v: float, bounds: tuple[float, float]) -> bool:
    return bounds[0] <= v <= bounds[1]


def _detect(e: float, n: float) -> tuple[str, float, float]:
    """Return ``(system, easting_or_lon, northing_or_lat)`` in the source system.

    Tries each candidate system in order; if E/N look swapped, swaps them.
    """
    # CH1903+ (LV95)
    if _in_range(e, _CH1903P_E) and _in_range(n, _CH1903P_N):
        return Participant.CoordSystem.CH1903PLUS, e, n
    if _in_range(n, _CH1903P_E) and _in_range(e, _CH1903P_N):
        return Participant.CoordSystem.CH1903PLUS, n, e

    # CH1903 (LV03)
    if _in_range(e, _CH1903_E) and _in_range(n, _CH1903_N):
        return Participant.CoordSystem.CH1903, e, n
    if _in_range(n, _CH1903_E) and _in_range(e, _CH1903_N):
        return Participant.CoordSystem.CH1903, n, e

    # WGS84 — easting field is longitude, northing field is latitude.
    if _in_range(e, _WGS84_LON) and _in_range(n, _WGS84_LAT):
        return Participant.CoordSystem.WGS84, e, n
    if _in_range(n, _WGS84_LON) and _in_range(e, _WGS84_LAT):
        return Participant.CoordSystem.WGS84, n, e

    raise CoordinateError(
        "Coordinates are outside the supported ranges for Switzerland "
        "(WGS84 lon 5.5–11 / lat 45.5–48; CH1903 470–860k/65–310k; "
        "CH1903+ 2.47–2.86M/1.065–1.31M)."
    )


def parse_coordinate_pair(e_input: str, n_input: str) -> ParsedCoordinates:
    """Parse two raw user inputs into canonical CH1903+ + WGS84 coordinates.

    The ``e_input`` field is what the user typed in the "Easting / longitude"
    box, ``n_input`` in "Northing / latitude". Either may carry apostrophes,
    commas, spaces, direction suffixes, etc.

    Raises :class:`CoordinateError` if the values fall outside the recognised
    ranges (which is also our sanity check that the location is actually
    in/near Switzerland — required by rule §3).
    """
    e_num = _to_float(e_input)
    n_num = _to_float(n_input)

    system, x, y = _detect(e_num, n_num)
    swapped = (x, y) != (e_num, n_num)

    if system == Participant.CoordSystem.CH1903PLUS:
        ch_e, ch_n = x, y
        lon, lat = _LV95_TO_WGS84.transform(ch_e, ch_n)
    elif system == Participant.CoordSystem.CH1903:
        ch_e, ch_n = _LV03_TO_LV95.transform(x, y)
        lon, lat = _LV95_TO_WGS84.transform(ch_e, ch_n)
    else:  # WGS84
        lon, lat = x, y
        ch_e, ch_n = _WGS84_TO_LV95.transform(lon, lat)

    return ParsedCoordinates(
        detected_system=system,
        ch1903p_e=round(ch_e, 2),
        ch1903p_n=round(ch_n, 2),
        wgs84_lat=round(lat, 6),
        wgs84_lon=round(lon, 6),
        swapped=swapped,
    )

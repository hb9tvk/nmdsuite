"""Server-side Swisstopo lookups (height + canton identify).

Mirrors what ``static/js/registration_map.js`` does client-side. We need the
same lookups server-side because .nmd uploads can carry ``#;KOORD_X=`` /
``#;KOORD_Y=`` lines that we accept as a location change for the
participant — when that happens the altitude and canton must be re-derived
authoritatively (the file's own ``QAH`` / ``KANTON`` are intentionally
ignored).

Both functions return ``None`` on any failure (network, parse, no-match).
Callers should treat None as "keep the previous value" rather than
clearing the field.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Final

from django.conf import settings

logger = logging.getLogger(__name__)

_TIMEOUT_S: Final = 5.0

# Bundesamt-für-Statistik canton number → ISO 3166-2:CH 2-letter code.
# Mirrors the table in registration_map.js (Swisstopo's swissBOUNDARIES3D
# layer returns the FSO number as ``ktnr``).
CANTON_BY_FSO: Final[dict[int, str]] = {
    1: "ZH", 2: "BE", 3: "LU", 4: "UR", 5: "SZ", 6: "OW", 7: "NW",
    8: "GL", 9: "ZG", 10: "FR", 11: "SO", 12: "BS", 13: "BL", 14: "SH",
    15: "AR", 16: "AI", 17: "SG", 18: "GR", 19: "AG", 20: "TG", 21: "TI",
    22: "VD", 23: "VS", 24: "NE", 25: "GE", 26: "JU",
}


def _http_get_json(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Swisstopo call failed (%s): %s", url, exc)
        return None


def lookup_altitude(ch1903p_e: float, ch1903p_n: float) -> int | None:
    """Query Swisstopo for the altitude (m a.s.l.) at an LV95 point."""
    base = getattr(settings, "SWISSTOPO_HEIGHT_API", None)
    if not base:
        return None
    url = f"{base}?easting={ch1903p_e}&northing={ch1903p_n}"
    data = _http_get_json(url)
    if not data or "height" not in data:
        return None
    try:
        return round(float(data["height"]))
    except (TypeError, ValueError):
        return None


def lookup_canton(ch1903p_e: float, ch1903p_n: float) -> str | None:
    """Query Swisstopo identify for the canton at an LV95 point. Returns a
    2-letter code, or None on failure / no match."""
    base = getattr(settings, "SWISSTOPO_IDENTIFY_API", None)
    if not base:
        return None
    pad = 100  # mapExtent box in metres around the point — keeps the lookup point-in-polygon.
    params = {
        "layers": "all:ch.swisstopo.swissboundaries3d-kanton-flaeche.fill",
        "geometry": f"{ch1903p_e},{ch1903p_n}",
        "geometryType": "esriGeometryPoint",
        "geometryFormat": "geojson",
        "sr": "2056",
        "mapExtent": f"{ch1903p_e - pad},{ch1903p_n - pad},{ch1903p_e + pad},{ch1903p_n + pad}",
        "imageDisplay": "200,200,96",
        "tolerance": "5",
        "returnGeometry": "false",
    }
    url = f"{base}?{urllib.parse.urlencode(params)}"
    data = _http_get_json(url)
    if not data:
        return None
    results = data.get("results") or []
    if not results:
        return None
    attrs = results[0].get("attributes") or results[0].get("properties") or {}
    return _extract_canton_code(attrs)


def _extract_canton_code(attrs: dict) -> str | None:
    for k in ("kanton", "abbreviation", "ktkz", "ktz", "code", "abbr"):
        v = attrs.get(k)
        if isinstance(v, str) and len(v) == 2 and v.isalpha():
            return v.upper()
    for k in ("ktnr", "kantonsnu", "kantonsnummer"):
        try:
            num = int(attrs.get(k))
        except (TypeError, ValueError):
            continue
        if num in CANTON_BY_FSO:
            return CANTON_BY_FSO[num]
    return None

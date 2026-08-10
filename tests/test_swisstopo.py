"""Unit tests for the server-side Swisstopo lookup helpers."""
from __future__ import annotations

import urllib.error
from unittest.mock import patch

from registration import swisstopo


# --- canton code extraction (pure function — no network) -------------------------------------


# Recorded verbatim from api3.geo.admin.ch for LV95 2727686/1172904
# (WGS84 46.69513/9.108104, Safiental GR) — the .nmd upload that exposed
# the parser gap. `ak` and `name`/`label` are the only canton-bearing
# fields the live layer returns; the original key list knew none of them,
# so every upload silently kept the participant's registered canton.
REAL_IDENTIFY_RESPONSE = {
    "results": [
        {
            "layerBodId": "ch.swisstopo.swissboundaries3d-kanton-flaeche.fill",
            "layerName": "Kantonsgrenzen",
            "featureId": 18,
            "id": 18,
            "properties": {
                "ak": "GR",
                "name": "Graubünden",
                "flaeche": 710530.0,
                "label": "Graubünden",
            },
        },
    ],
}


def test_lookup_canton_reads_the_real_swisstopo_payload():
    """The regression that matters: the whole path, against what the live
    API actually answers."""
    with patch.object(swisstopo, "_http_get_json", return_value=REAL_IDENTIFY_RESPONSE):
        assert swisstopo.lookup_canton(2_727_686, 1_172_904) == "GR"


def test_extract_canton_from_ak_key():
    """`ak` is the field the live layer uses — checked before anything else."""
    assert swisstopo._extract_canton_code({"ak": "GR"}) == "GR"
    assert swisstopo._extract_canton_code({"ak": "gr"}) == "GR"


def test_extract_canton_from_abbreviation_key():
    assert swisstopo._extract_canton_code({"kanton": "be"}) == "BE"


def test_extract_canton_from_fso_number():
    # Bern = FSO 2.
    assert swisstopo._extract_canton_code({"ktnr": 2}) == "BE"


def test_extract_canton_falls_back_to_the_full_name():
    """Last resort, for a response carrying no code at all."""
    assert swisstopo._extract_canton_code({"name": "Graubünden"}) == "GR"
    assert swisstopo._extract_canton_code({"label": "Ticino"}) == "TI"
    # Unaccented and non-German spellings of the same canton.
    assert swisstopo._extract_canton_code({"name": "graubunden"}) == "GR"
    assert swisstopo._extract_canton_code({"name": "Tessin"}) == "TI"


def test_extract_canton_prefers_the_code_over_the_name():
    """Both are present in every real response; disagreement should never
    happen, but the authoritative field is the code."""
    assert swisstopo._extract_canton_code({"ak": "GR", "name": "Ticino"}) == "GR"


def test_extract_canton_returns_none_for_garbage():
    assert swisstopo._extract_canton_code({}) is None
    assert swisstopo._extract_canton_code({"kanton": "BEE"}) is None  # too long
    assert swisstopo._extract_canton_code({"ktnr": "abc"}) is None
    assert swisstopo._extract_canton_code({"name": "Bavaria"}) is None


# --- HTTP wrappers ---------------------------------------------------------------------------


def test_lookup_altitude_returns_int_on_success():
    fake = {"height": "1234.7"}
    with patch.object(swisstopo, "_http_get_json", return_value=fake):
        assert swisstopo.lookup_altitude(2_600_000, 1_200_000) == 1235


def test_lookup_altitude_returns_none_on_http_failure():
    with patch.object(swisstopo, "_http_get_json", return_value=None):
        assert swisstopo.lookup_altitude(2_600_000, 1_200_000) is None


def test_lookup_altitude_returns_none_on_missing_height_field():
    with patch.object(swisstopo, "_http_get_json", return_value={"other": 1}):
        assert swisstopo.lookup_altitude(2_600_000, 1_200_000) is None


def test_lookup_canton_returns_code_on_success():
    fake = {"results": [{"attributes": {"kanton": "ur"}}]}
    with patch.object(swisstopo, "_http_get_json", return_value=fake):
        assert swisstopo.lookup_canton(2_700_000, 1_180_000) == "UR"


def test_lookup_canton_returns_none_when_no_results():
    with patch.object(swisstopo, "_http_get_json", return_value={"results": []}):
        assert swisstopo.lookup_canton(2_700_000, 1_180_000) is None


def test_http_get_json_swallows_url_error():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
        assert swisstopo._http_get_json("http://example.invalid/x") is None

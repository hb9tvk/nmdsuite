"""Unit tests for the server-side Swisstopo lookup helpers."""
from __future__ import annotations

import urllib.error
from unittest.mock import patch

from registration import swisstopo


# --- canton code extraction (pure function — no network) -------------------------------------


def test_extract_canton_from_abbreviation_key():
    assert swisstopo._extract_canton_code({"kanton": "be"}) == "BE"


def test_extract_canton_from_fso_number():
    # Bern = FSO 2.
    assert swisstopo._extract_canton_code({"ktnr": 2}) == "BE"


def test_extract_canton_returns_none_for_garbage():
    assert swisstopo._extract_canton_code({}) is None
    assert swisstopo._extract_canton_code({"kanton": "BEE"}) is None  # too long
    assert swisstopo._extract_canton_code({"ktnr": "abc"}) is None


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

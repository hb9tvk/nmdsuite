"""The shared map-drawing engine.

Pure arithmetic on the two surveyed points, the label sizing rule, and a
render smoke test. Nothing here touches the database — the engine takes
plain labels and coordinates, so both map artefacts inherit whatever this
file proves.
"""
from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader

from core.map_render import (
    IMAGE_HEIGHT_PX,
    IMAGE_WIDTH_PX,
    LABEL_CAP_HEIGHT_RATIO,
    LABEL_HEIGHT_OF_DIAMETER,
    LOGO_CENTRE_EAST,
    LOGO_PATH,
    GeoReference,
    MapStation,
    _label_metrics,
    _logo_rect,
    render_station_map,
)
from registration.forms import QRB_THRESHOLD_M

PAGE_W, PAGE_H = landscape(A4)

# The two points the georeference was surveyed from.
CAL_A_PX, CAL_A_LV03 = (263.0, 660.0), (570082.0, 109691.0)
CAL_B_PX, CAL_B_LV03 = (755.0, 306.0), (725955.0, 221524.0)


def _pdf_text(blob: bytes) -> str:
    reader = PdfReader(BytesIO(blob))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _image_count(blob: bytes) -> int:
    """How many bitmaps the first page draws.

    Read straight off the page resources rather than through pypdf's image
    decoding, which would drag in an optional imaging dependency just to
    count them.
    """
    page = PdfReader(BytesIO(blob)).pages[0]
    xobjects = page["/Resources"].get("/XObject", {})
    return sum(
        1 for name in xobjects
        if xobjects[name].get_object().get("/Subtype") == "/Image"
    )


# --- georeference ----------------------------------------------------------------------------


def _expected_canvas(px_x: float, px_y: float) -> tuple[float, float]:
    """Where a given image pixel lands on the canvas, independent of the
    coordinate maths — the image is stretched to fill the page and the
    vertical axis flips."""
    return px_x * PAGE_W / IMAGE_WIDTH_PX, PAGE_H - px_y * PAGE_H / IMAGE_HEIGHT_PX


@pytest.mark.parametrize(
    "lv03,px", [(CAL_A_LV03, CAL_A_PX), (CAL_B_LV03, CAL_B_PX)],
)
def test_calibration_points_land_on_their_pixels(lv03, px):
    """The transform must reproduce the two points it was derived from."""
    geo = GeoReference(canvas_width=PAGE_W, canvas_height=PAGE_H)
    got_x, got_y = geo.to_canvas(*lv03)
    want_x, want_y = _expected_canvas(*px)
    assert got_x == pytest.approx(want_x, abs=0.01)
    assert got_y == pytest.approx(want_y, abs=0.01)


def test_north_is_up_and_east_is_right():
    geo = GeoReference(canvas_width=PAGE_W, canvas_height=PAGE_H)
    west_x, _ = geo.to_canvas(600_000, 200_000)
    east_x, _ = geo.to_canvas(700_000, 200_000)
    _, south_y = geo.to_canvas(600_000, 100_000)
    _, north_y = geo.to_canvas(600_000, 200_000)
    assert east_x > west_x
    assert north_y > south_y


def test_circle_radius_matches_the_qrb_threshold():
    """A circle's diameter must be the distance the registration form warns
    below, so that overlap on the map means exactly that warning fired."""
    geo = GeoReference(canvas_width=PAGE_W, canvas_height=PAGE_H)
    radius = geo.metres_to_canvas(QRB_THRESHOLD_M / 2.0)

    # Two stations exactly the threshold apart: their circles must touch,
    # not overlap. Compare against the horizontal canvas distance.
    left_x, _ = geo.to_canvas(600_000, 200_000)
    right_x, _ = geo.to_canvas(600_000 + QRB_THRESHOLD_M, 200_000)
    assert (right_x - left_x) == pytest.approx(2 * radius, rel=0.005)


def test_metres_to_canvas_is_isotropic():
    """Circles must not come out as ellipses."""
    geo = GeoReference(canvas_width=PAGE_W, canvas_height=PAGE_H)
    length = geo.metres_to_canvas(10_000)

    x0, y0 = geo.to_canvas(600_000, 200_000)
    x1, _ = geo.to_canvas(610_000, 200_000)
    _, y1 = geo.to_canvas(600_000, 210_000)
    assert (x1 - x0) == pytest.approx(length, rel=0.005)
    assert (y1 - y0) == pytest.approx(length, rel=0.005)


# --- label sizing ----------------------------------------------------------------------------


def test_label_text_height_is_a_fixed_share_of_the_circle():
    """Labels are sized against the circles, not fixed in points. What the
    reader compares to a circle is the cap height — uppercase and digits
    only — which runs shorter than the em reportlab is handed."""
    geo = GeoReference(canvas_width=PAGE_W, canvas_height=PAGE_H)
    font_size, line_height = _label_metrics(geo)

    diameter = geo.metres_to_canvas(QRB_THRESHOLD_M)
    cap_height = font_size * LABEL_CAP_HEIGHT_RATIO
    assert cap_height == pytest.approx(LABEL_HEIGHT_OF_DIAMETER * diameter, rel=1e-6)
    # The reserved row must leave room for the glyphs it holds.
    assert line_height > cap_height


# --- rendering -------------------------------------------------------------------------------


def test_render_draws_labels_and_headings():
    blob = render_station_map(
        year=2026,
        stations=[
            MapStation(label="ALPHA-ONE", east=600_000, north=200_000),
            MapStation(label="BETA-TWO", east=700_000, north=150_000),
        ],
        subtitles=("Erste Zeile", "Deuxième ligne", "Terza riga"),
        doc_title="whatever",
    )
    assert blob[:4] == b"%PDF"
    text = _pdf_text(blob)
    assert "ALPHA-ONE" in text
    assert "BETA-TWO" in text
    assert "National Mountain Day 2026" in text
    for line in ("Erste Zeile", "Deuxième ligne", "Terza riga"):
        assert line in text


def test_render_prints_the_legend_when_given_one():
    """The legend is what tells a reader how to decode a label, so it must
    survive onto the sheet rather than being silently dropped."""
    blob = render_station_map(
        year=2026,
        stations=[MapStation(label="ALPHA-ONE", east=600_000, north=200_000)],
        subtitles=("Titelzeile",),
        legend=("HOW-TO-READ-THIS",),
        doc_title="whatever",
    )
    text = _pdf_text(blob)
    assert "HOW-TO-READ-THIS" in text
    # The heading block flows downward, so the timestamp still has to fit
    # underneath the extra line rather than being overprinted.
    assert "Stand / mise à jour / aggiornamento:" in text


def test_render_draws_the_relief_and_the_club_emblem():
    """Two bitmaps on the sheet: the background and the USKA logo. Both
    assets ship in the repo, so a missing one is a packaging mistake
    rather than an acceptable degradation."""
    assert LOGO_PATH.exists(), "USKA logo asset is missing from static/img/"
    blob = render_station_map(
        year=2026,
        stations=[MapStation(label="ALPHA-ONE", east=600_000, north=200_000)],
        subtitles=("Titelzeile",),
        doc_title="whatever",
    )
    assert _image_count(blob) == 2


def test_logo_keeps_its_aspect_ratio():
    """A squashed club emblem is worse than none — the drawn box has to
    follow the asset's own proportions, not a hardcoded width."""
    geo = GeoReference(canvas_width=PAGE_W, canvas_height=PAGE_H)
    _x, _y, width, height = _logo_rect(geo)

    px_width, px_height = ImageReader(str(LOGO_PATH)).getSize()
    assert width / height == pytest.approx(px_width / px_height)


def test_logo_straddles_the_800_easting():
    """Placed against the map rather than the sheet edge, matching the
    hand-drawn original. Checked through the georeference, so a page-size
    change has to keep it on the ruler rather than drift off it."""
    geo = GeoReference(canvas_width=PAGE_W, canvas_height=PAGE_H)
    x, y, width, height = _logo_rect(geo)

    ruler_x, _ = geo.to_canvas(LOGO_CENTRE_EAST, 0)
    assert x + width / 2 == pytest.approx(ruler_x, abs=0.5)
    # Wholly on the sheet, and in its upper half.
    assert x > 0
    assert x + width < PAGE_W
    assert y > PAGE_H / 2
    assert y + height < PAGE_H


def test_render_handles_no_stations():
    blob = render_station_map(
        year=2026, stations=[], subtitles=("Leer",), doc_title="whatever",
    )
    assert blob[:4] == b"%PDF"
    assert len(PdfReader(BytesIO(blob)).pages) == 1

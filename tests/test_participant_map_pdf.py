"""Participant map PDF.

Two layers: the georeference (pure arithmetic on the two surveyed points)
and the rendered document.
"""
from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4, landscape

from core.models import Contest, Participant
from core.participant_map_pdf import (
    IMAGE_HEIGHT_PX,
    IMAGE_WIDTH_PX,
    LABEL_CAP_HEIGHT_RATIO,
    LABEL_HEIGHT_OF_DIAMETER,
    GeoReference,
    _label_metrics,
    build_participant_map_pdf,
)
from registration.forms import QRB_THRESHOLD_M

User = get_user_model()

PAGE_W, PAGE_H = landscape(A4)

# The two points the georeference was surveyed from.
CAL_A_PX, CAL_A_LV03 = (263.0, 660.0), (570082.0, 109691.0)
CAL_B_PX, CAL_B_LV03 = (755.0, 306.0), (725955.0, 221524.0)


def _pdf_text(blob: bytes) -> str:
    reader = PdfReader(BytesIO(blob))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _make_participant(
    contest, *, callsign, lv95_e=2_681_239, lv95_n=1_237_065, cancelled=False,
) -> Participant:
    """LV95 in, since that is what registration stores. The map reads the
    LV03 properties derived from it."""
    username = callsign.replace("/", "")
    user = User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org",
    )
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign,
        first_name="Op", email=f"{username.lower()}@x.org",
        coord_system_input="ch1903plus",
        coord_input_e=str(lv95_e), coord_input_n=str(lv95_n),
        ch1903p_e=lv95_e, ch1903p_n=lv95_n,
        wgs84_lat=46.8, wgs84_lon=8.5,
        altitude_m=1500, canton="ZH", operating_modes=3,
        cancelled_at=timezone.now() if cancelled else None,
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


def test_label_text_height_is_a_fixed_share_of_the_circle():
    """Callsigns are sized against the circles, not fixed in points. What
    the reader compares to a circle is the cap height — uppercase and
    digits only — which runs shorter than the em reportlab is handed."""
    geo = GeoReference(canvas_width=PAGE_W, canvas_height=PAGE_H)
    font_size, line_height = _label_metrics(geo)

    diameter = geo.metres_to_canvas(QRB_THRESHOLD_M)
    cap_height = font_size * LABEL_CAP_HEIGHT_RATIO
    assert cap_height == pytest.approx(LABEL_HEIGHT_OF_DIAMETER * diameter, rel=1e-6)
    # The reserved row must leave room for the glyphs it holds.
    assert line_height > cap_height


# --- document --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_build_map_returns_valid_pdf_bytes(seeded_contest):
    _make_participant(seeded_contest, callsign="HB9TVK")
    blob = build_participant_map_pdf(seeded_contest)
    assert blob[:4] == b"%PDF"
    assert len(PdfReader(BytesIO(blob)).pages) == 1


@pytest.mark.django_db
def test_map_shows_every_active_callsign(seeded_contest):
    _make_participant(seeded_contest, callsign="HB9AAA")
    _make_participant(seeded_contest, callsign="HB9BBB", lv95_e=2_600_000, lv95_n=1_200_000)
    text = _pdf_text(build_participant_map_pdf(seeded_contest))
    assert "HB9AAA" in text
    assert "HB9BBB" in text


@pytest.mark.django_db
def test_map_omits_cancelled_participants(seeded_contest):
    _make_participant(seeded_contest, callsign="HB9AAA")
    _make_participant(
        seeded_contest, callsign="HB9GONE", lv95_e=2_600_000, lv95_n=1_200_000,
        cancelled=True,
    )
    text = _pdf_text(build_participant_map_pdf(seeded_contest))
    assert "HB9AAA" in text
    assert "HB9GONE" not in text


@pytest.mark.django_db
def test_map_skips_participants_without_coordinates(seeded_contest):
    """Defensive: a circle in the Mediterranean is worse than none."""
    p = _make_participant(seeded_contest, callsign="HB9NOPOS")
    p.ch1903p_e = None
    p.ch1903p_n = None
    p.save(update_fields=["ch1903p_e", "ch1903p_n"])

    blob = build_participant_map_pdf(seeded_contest)
    assert blob[:4] == b"%PDF"
    assert "HB9NOPOS" not in _pdf_text(blob)


@pytest.mark.django_db
def test_map_renders_with_no_participants(seeded_contest):
    """Before anyone registers, the map is still a valid document."""
    blob = build_participant_map_pdf(seeded_contest)
    assert blob[:4] == b"%PDF"


# --- views -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_map_download_requires_login(client, seeded_contest):
    response = client.get("/submission/participant-map.pdf")
    assert response.status_code in (301, 302)
    assert "/submission/login/" in response["Location"]


@pytest.mark.django_db
def test_map_download_blocked_while_registration_open(client, seeded_contest):
    """Same gate as the list: no publishing an incomplete roster."""
    p = _make_participant(seeded_contest, callsign="HB9TVK")
    client.force_login(p.user)
    response = client.get("/submission/participant-map.pdf")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")


@pytest.mark.django_db
def test_map_download_available_after_registration_closed(client, seeded_contest):
    p = _make_participant(seeded_contest, callsign="HB9TVK")
    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save(update_fields=["state"])

    client.force_login(p.user)
    response = client.get("/submission/participant-map.pdf")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert f"nmd-{seeded_contest.year}-map.pdf" in response["Content-Disposition"]


@pytest.mark.django_db
def test_admin_map_preview_renders_regardless_of_state(client, seeded_contest):
    """Staff need to see a crowded map *before* closing registration —
    that transition mails the map out."""
    assert seeded_contest.state == Contest.State.REGISTRATION_OPEN
    _make_participant(seeded_contest, callsign="HB9TVK")
    staff = User.objects.create_user(
        username="STAFF", password="x", email="s@x.org", is_staff=True,
    )
    client.force_login(staff)
    response = client.get("/admin/participant-map-preview.pdf")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")


@pytest.mark.django_db
def test_admin_map_preview_requires_staff(client, seeded_contest):
    p = _make_participant(seeded_contest, callsign="HB9TVK")
    client.force_login(p.user)
    response = client.get("/admin/participant-map-preview.pdf")
    assert response.status_code in (301, 302, 403)


@pytest.mark.django_db
def test_map_carries_trilingual_title_and_legend(seeded_contest):
    _make_participant(seeded_contest, callsign="HB9TVK")
    text = _pdf_text(build_participant_map_pdf(seeded_contest))
    assert f"National Mountain Day {seeded_contest.year}" in text
    assert "Angemeldete Stationen" in text
    assert "Stations inscrites" in text
    assert "Stazioni iscritte" in text
    # Legend states the circle diameter, derived from the QRB threshold.
    assert f"{QRB_THRESHOLD_M // 1000} km" in text

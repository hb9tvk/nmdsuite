"""Participant map PDF.

Which stations the pre-contest map shows, and who may download it. The
drawing itself — georeference, label sizing, rendering — is covered once
in ``test_map_render.py`` for both map artefacts.
"""
from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from pypdf import PdfReader

from core.models import Contest, Participant
from core.participant_map_pdf import build_participant_map_pdf
from public.ranking_map_pdf import LEGEND as RANKING_LEGEND
from registration.forms import QRB_THRESHOLD_M

User = get_user_model()


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


@pytest.mark.django_db
def test_participant_map_carries_no_ranks(seeded_contest):
    """The pre-contest sheet predates any result — ranks, and the legend
    explaining how to read them, belong only to the ranking map."""
    _make_participant(seeded_contest, callsign="HB9TVK")
    text = _pdf_text(build_participant_map_pdf(seeded_contest))
    for line in RANKING_LEGEND:
        assert line not in text
    assert "Rangliste" not in text


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

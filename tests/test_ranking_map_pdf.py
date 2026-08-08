"""Ranking map PDF.

The post-contest sheet: the participant map redrawn with each station's
placing folded into its label. Drawing mechanics live in
``test_map_render.py``; this file covers the label convention, who gets
onto the sheet, and the download gates.
"""
from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from pypdf import PdfReader

from core.models import Contest, Participant, QsoEntry, ScoringRecord
from public.ranking_map_pdf import (
    StationRanks,
    build_ranking_map_pdf,
    rank_label,
    station_ranks,
)

User = get_user_model()


def _pdf_text(blob: bytes) -> str:
    """Extracted text, whitespace-normalised so label spacing survives."""
    reader = PdfReader(BytesIO(blob))
    raw = "\n".join(page.extract_text() or "" for page in reader.pages)
    return " ".join(raw.split())


def _make_participant(
    contest, *, callsign, lv95_e=2_681_239, lv95_n=1_237_065,
    submitted=True, cancelled=False,
) -> Participant:
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
        submitted_at=timezone.now() if submitted else None,
        cancelled_at=timezone.now() if cancelled else None,
    )


def _score(participant, *, mode, points):
    """Attach one scored QSO, shortcutting a full scoring pass."""
    qso = QsoEntry.objects.create(
        participant=participant,
        utc_raw="0700",
        utc_time=timezone.now(),
        mode=mode,
        remote_call="HB9X/P",
        rsts="59" if mode == "SSB" else "599",
        txts="text " * 4,
        rstr="59" if mode == "SSB" else "599",
        txtr="reply " * 3,
    )
    ScoringRecord.objects.create(qso=qso, status="full_match", points=points)
    return qso


@pytest.fixture
def ranked_contest(seeded_contest):
    """Three scoring stations laid out so every label shape appears:

    ALPHA is first in CW and second in SSB, BRAVO is CW-only, CHARLIE is
    SSB-only. DELTA submits a log but scores nothing.
    """
    alpha = _make_participant(seeded_contest, callsign="HB9ALPHA")
    bravo = _make_participant(
        seeded_contest, callsign="HB9BRAVO", lv95_e=2_600_000, lv95_n=1_200_000,
    )
    charlie = _make_participant(
        seeded_contest, callsign="HB9CHARLIE", lv95_e=2_750_000, lv95_n=1_150_000,
    )
    _make_participant(
        seeded_contest, callsign="HB9DELTA", lv95_e=2_700_000, lv95_n=1_100_000,
    )

    _score(alpha, mode="CW", points=12)
    _score(alpha, mode="SSB", points=4)
    _score(bravo, mode="CW", points=8)
    _score(charlie, mode="SSB", points=9)

    seeded_contest.state = Contest.State.PUBLISHED
    seeded_contest.results_published_at = timezone.now()
    seeded_contest.save(update_fields=["state", "results_published_at"])
    return seeded_contest


# --- the label convention --------------------------------------------------------------------


def test_rank_label_carries_both_placings():
    """CW rank leads, SSB rank trails, both marked as ordinals."""
    assert rank_label(StationRanks("HB9BXE", cw=4, ssb=12)) == "4. HB9BXE 12."


def test_rank_label_drops_the_mode_a_station_is_not_ranked_in():
    assert rank_label(StationRanks("HB9BXE", cw=4, ssb=None)) == "4. HB9BXE"
    assert rank_label(StationRanks("HB9BXE", cw=None, ssb=3)) == "HB9BXE 3."


def test_rank_label_position_distinguishes_the_two_modes():
    """The same number means different things on either side of the
    callsign, which is the whole point of the convention."""
    cw_only = rank_label(StationRanks("HB9BXE", cw=7, ssb=None))
    ssb_only = rank_label(StationRanks("HB9BXE", cw=None, ssb=7))
    assert cw_only != ssb_only


# --- who gets a circle -----------------------------------------------------------------------


@pytest.mark.django_db
def test_station_ranks_reports_each_mode_independently(ranked_contest):
    by_call = {r.callsign: r for r in station_ranks(ranked_contest)}

    assert by_call["HB9ALPHA"].cw == 1
    assert by_call["HB9ALPHA"].ssb == 2
    assert by_call["HB9BRAVO"].cw == 2
    assert by_call["HB9BRAVO"].ssb is None
    assert by_call["HB9CHARLIE"].cw is None
    assert by_call["HB9CHARLIE"].ssb == 1


@pytest.mark.django_db
def test_station_ranks_omits_stations_with_no_placing(ranked_contest):
    """A submitted log that scored nothing earns no rank, so it carries no
    result to show on a sheet titled "Rangliste"."""
    assert "HB9DELTA" not in {r.callsign for r in station_ranks(ranked_contest)}


@pytest.mark.django_db
def test_station_ranks_is_deterministic(ranked_contest):
    """Same results must always yield the same sheet."""
    first = [(r.callsign, r.cw, r.ssb) for r in station_ranks(ranked_contest)]
    second = [(r.callsign, r.cw, r.ssb) for r in station_ranks(ranked_contest)]
    assert first == second


# --- document --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_map_prints_every_label_shape(ranked_contest):
    text = _pdf_text(build_ranking_map_pdf(ranked_contest))
    assert "1. HB9ALPHA 2." in text
    assert "2. HB9BRAVO" in text
    assert "HB9CHARLIE 1." in text


@pytest.mark.django_db
def test_map_omits_unranked_stations(ranked_contest):
    assert "HB9DELTA" not in _pdf_text(build_ranking_map_pdf(ranked_contest))


@pytest.mark.django_db
def test_map_omits_cancelled_stations(ranked_contest):
    """Cancelling after scoring must remove the station from the sheet,
    not leave a rank nobody can look up."""
    p = Participant.objects.get(contest=ranked_contest, callsign="HB9BRAVO")
    p.cancelled_at = timezone.now()
    p.save(update_fields=["cancelled_at"])

    assert "HB9BRAVO" not in _pdf_text(build_ranking_map_pdf(ranked_contest))


@pytest.mark.django_db
def test_map_skips_ranked_stations_without_coordinates(ranked_contest):
    """Defensive, matching the participant map: no circle in the sea."""
    p = Participant.objects.get(contest=ranked_contest, callsign="HB9BRAVO")
    p.ch1903p_e = None
    p.ch1903p_n = None
    p.save(update_fields=["ch1903p_e", "ch1903p_n"])

    blob = build_ranking_map_pdf(ranked_contest)
    assert blob[:4] == b"%PDF"
    assert "HB9BRAVO" not in _pdf_text(blob)


@pytest.mark.django_db
def test_map_explains_how_to_read_a_label(ranked_contest):
    """Without the legend the two numbers are ambiguous."""
    text = _pdf_text(build_ranking_map_pdf(ranked_contest))
    assert "Rangliste und Standorte" in text
    assert "Classement et QTH" in text
    assert "Classifica e QTH" in text
    assert "<Rang CW> QRA <Rang SSB>" in text


@pytest.mark.django_db
def test_map_renders_before_anyone_has_scored(seeded_contest):
    """An empty ranking is still a valid document."""
    blob = build_ranking_map_pdf(seeded_contest)
    assert blob[:4] == b"%PDF"
    assert len(PdfReader(BytesIO(blob)).pages) == 1


# --- views -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_ranking_map_download_requires_login(client, seeded_contest):
    response = client.get("/submission/ranking-map.pdf")
    assert response.status_code in (301, 302)
    assert "/submission/login/" in response["Location"]


@pytest.mark.django_db
def test_ranking_map_blocked_before_results_are_published(client, seeded_contest):
    """Before publication there are no ranks to draw."""
    p = _make_participant(seeded_contest, callsign="HB9TVK")
    client.force_login(p.user)
    response = client.get("/submission/ranking-map.pdf")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")


@pytest.mark.django_db
def test_ranking_map_available_once_published(client, ranked_contest):
    p = Participant.objects.get(contest=ranked_contest, callsign="HB9ALPHA")
    client.force_login(p.user)
    response = client.get("/submission/ranking-map.pdf")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert f"nmd-{ranked_contest.year}-ranking-map.pdf" in response["Content-Disposition"]


@pytest.mark.django_db
def test_admin_ranking_map_preview_renders_before_publication(client, seeded_contest):
    """Staff need a look before the publish transition mails it out."""
    assert seeded_contest.state == Contest.State.REGISTRATION_OPEN
    _make_participant(seeded_contest, callsign="HB9TVK")
    staff = User.objects.create_user(
        username="STAFF", password="x", email="s@x.org", is_staff=True,
    )
    client.force_login(staff)
    response = client.get("/admin/ranking-map.pdf")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")


@pytest.mark.django_db
def test_admin_ranking_map_preview_denied_to_participants(client, seeded_contest):
    p = _make_participant(seeded_contest, callsign="HB9TVK")
    client.force_login(p.user)
    response = client.get("/admin/ranking-map.pdf")
    assert response.status_code in (301, 302, 403)

"""Ranking PDF (F4 — magazine export).

Service-level: build_ranking_pdf returns a syntactically valid PDF
that contains the participants and the section titles. View-level: the
admin copy at /admin/ranking.pdf, reachable at any contest state for
magazine pre-prints, and the participants' copy at
/submission/ranking.pdf, which is gated on publication.
"""
from __future__ import annotations

import re
from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from pypdf import PdfReader

from core.models import Contest
from public.ranking_pdf import build_ranking_pdf
from tests.test_public_ranking import _add_qso, _make_participant

User = get_user_model()


def _pdf_text(blob: bytes) -> str:
    reader = PdfReader(BytesIO(blob))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _squashed(blob: bytes) -> str:
    """Extracted text with every whitespace run removed.

    A cell wraps inside its column, so a long value comes back out of the
    extractor broken across lines. Whether a value reached the page is a
    separate question from where it happened to break.
    """
    return "".join(_pdf_text(blob).split())


def _pages(blob: bytes) -> list[tuple[str, bool]]:
    """One ``(text, is_landscape)`` pair per page, in order."""
    return [
        (page.extract_text() or "", page.mediabox.width > page.mediabox.height)
        for page in PdfReader(BytesIO(blob)).pages
    ]


_GENERATED_AT = re.compile(r"Stand \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC")


def _without_timestamp(text: str) -> str:
    """Drop the generation stamp before comparing two renders.

    It is minute-precision, so two renders in one test can straddle a
    boundary — comparing it would make the test flaky about the clock
    rather than strict about the content.
    """
    return _GENERATED_AT.sub("", text)


# --- service ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_build_pdf_returns_valid_pdf_bytes(seeded_contest):
    _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    blob = build_ranking_pdf(seeded_contest)
    assert blob.startswith(b"%PDF-")
    assert blob.rstrip().endswith(b"%%EOF")
    assert len(blob) > 1000


@pytest.mark.django_db
def test_build_pdf_works_on_empty_contest(seeded_contest):
    """No participants at all — still produces a valid PDF (just the
    headers + an em-dash placeholder row in each table)."""
    blob = build_ranking_pdf(seeded_contest)
    assert blob.startswith(b"%PDF-")
    text = _pdf_text(blob)
    assert "CW" in text
    assert "SSB" in text


@pytest.mark.django_db
def test_build_pdf_lists_submitted_participants_with_qsos(seeded_contest):
    p_cw = _make_participant(
        seeded_contest, username="HB9CW", callsign="HB9CW/P", modes=1,
    )
    p_ssb = _make_participant(
        seeded_contest, username="HB9SSB", callsign="HB9SSB/P", modes=2,
    )
    _add_qso(p_cw, mode="CW", points=4)
    _add_qso(p_ssb, mode="SSB", points=4)

    text = _pdf_text(build_ranking_pdf(seeded_contest))
    assert "HB9CW/P" in text
    assert "HB9SSB/P" in text


@pytest.mark.django_db
def test_build_pdf_omits_cancelled_participants(seeded_contest):
    active = _make_participant(seeded_contest, username="HB9OK", callsign="HB9OK/P")
    _make_participant(
        seeded_contest, username="HB9CX", callsign="HB9CX/P", cancelled=True,
    )
    _add_qso(active, mode="CW", points=4)
    text = _pdf_text(build_ranking_pdf(seeded_contest))
    assert "HB9OK/P" in text
    assert "HB9CX/P" not in text


# --- service: the station-data section ---------------------------------------------------------


@pytest.mark.django_db
def test_station_data_starts_a_new_landscape_page(seeded_contest):
    """Ten columns don't fit portrait, so the section turns the sheet — and
    a turned sheet can't share a page with the rankings above it."""
    p = _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P")
    _add_qso(p, mode="CW", points=4)

    pages = _pages(build_ranking_pdf(seeded_contest))
    ranking = [(text, landscape) for text, landscape in pages if "Rang" in text]
    station = [(text, landscape) for text, landscape in pages if "Station data" in text]

    assert ranking and station
    assert all(not landscape for _text, landscape in ranking)
    assert all(landscape for _text, landscape in station)
    # The rankings and the station data never share a sheet.
    assert all("Rang" not in text for text, _landscape in station)


@pytest.mark.django_db
def test_station_data_carries_feedline_masts_and_guying(seeded_contest):
    p = _make_participant(
        seeded_contest, username="HB9A", callsign="HB9A/P",
        components={
            1: "TRX-DESCRIPTION",
            6: "FEEDLINE-DESCRIPTION",
            7: "MASTS-DESCRIPTION",
            8: "GUYING-DESCRIPTION",
        },
    )
    _add_qso(p, mode="CW", points=4)

    text = _squashed(build_ranking_pdf(seeded_contest))
    for value in (
        "TRX-DESCRIPTION", "FEEDLINE-DESCRIPTION",
        "MASTS-DESCRIPTION", "GUYING-DESCRIPTION",
    ):
        assert value in text


@pytest.mark.django_db
def test_station_data_survives_markup_characters(seeded_contest):
    """Kit descriptions are operator free text and land in a reportlab
    paragraph, which reads its content as mini-HTML."""
    p = _make_participant(
        seeded_contest, username="HB9A", callsign="HB9A/P",
        components={5: "Balun & <1:4>"},
    )
    _add_qso(p, mode="CW", points=4)

    text = _pdf_text(build_ranking_pdf(seeded_contest))
    assert "Balun & <1:4>" in text


@pytest.mark.django_db
def test_station_data_may_span_several_pages(seeded_contest, settings):
    """A large field must flow onto further landscape pages rather than
    being cut off at the first."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    field = 60
    for i in range(field):
        p = _make_participant(
            seeded_contest, username=f"HB9X{i:02d}", callsign=f"HB9X{i:02d}/P",
        )
        _add_qso(p, mode="CW", points=4)

    pages = _pages(build_ranking_pdf(seeded_contest))
    station = [text for text, landscape in pages if landscape]
    assert len(station) > 1
    # Each continuation page repeats the header row, and every station made
    # it onto one of them.
    assert all("Rufzeichen" in text for text in station)
    joined = "\n".join(station)
    for i in range(field):
        assert f"HB9X{i:02d}/P" in joined


# --- view: gating ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_ranking_pdf_requires_staff(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    client.force_login(p.user)  # logged in, but not staff
    response = client.get("/admin/ranking.pdf")
    assert response.status_code in (301, 302, 403)


@pytest.mark.django_db
def test_ranking_pdf_streams_pdf_to_staff(client, seeded_contest):
    _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    staff = User.objects.create_user(
        username="STAFF", password="x", email="s@x.org", is_staff=True,
    )
    client.force_login(staff)
    response = client.get("/admin/ranking.pdf")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert f"nmd-{seeded_contest.year}-ranking.pdf" in response["Content-Disposition"]


@pytest.mark.django_db
def test_ranking_pdf_works_in_any_state(client, seeded_contest):
    """Admin judgement: the PDF is reachable even before publish (we don't
    want to gate magazine pre-prints on the public-ranking flag)."""
    assert seeded_contest.state == Contest.State.REGISTRATION_OPEN
    staff = User.objects.create_user(
        username="STAFF", password="x", email="s@x.org", is_staff=True,
    )
    client.force_login(staff)
    response = client.get("/admin/ranking.pdf")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")


# --- view: the participants' copy -------------------------------------------------------------
#
# Same document, offered in the portal beside the ranking map. Unlike the
# admin route this one *is* gated on publication: operators must not see a
# ranking that is still being corrected.


def _publish(contest):
    contest.state = Contest.State.PUBLISHED
    contest.results_published_at = timezone.now()
    contest.save(update_fields=["state", "results_published_at"])
    return contest


@pytest.mark.django_db
def test_portal_ranking_pdf_requires_login(client, seeded_contest):
    response = client.get("/submission/ranking.pdf")
    assert response.status_code in (301, 302)
    assert "/submission/login/" in response["Location"]


@pytest.mark.django_db
def test_portal_ranking_pdf_blocked_before_publication(client, seeded_contest):
    """The admin route stays open at any state for magazine pre-prints;
    the participants' route must not."""
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    client.force_login(p.user)
    response = client.get("/submission/ranking.pdf")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")


@pytest.mark.django_db
def test_portal_ranking_pdf_streams_once_published(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _add_qso(p, mode="CW", points=4)
    _publish(seeded_contest)

    client.force_login(p.user)
    response = client.get("/submission/ranking.pdf")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert f"nmd-{seeded_contest.year}-ranking.pdf" in response["Content-Disposition"]


@pytest.mark.django_db
def test_portal_and_admin_ranking_pdfs_are_the_same_document(client, seeded_contest):
    """One artefact, two doors — the operators' copy must not quietly
    become a different or reduced document from the magazine's."""
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _add_qso(p, mode="CW", points=4)
    _publish(seeded_contest)
    staff = User.objects.create_user(
        username="STAFF", password="x", email="s@x.org", is_staff=True,
    )

    client.force_login(p.user)
    portal_text = _pdf_text(client.get("/submission/ranking.pdf").content)
    client.force_login(staff)
    admin_text = _pdf_text(client.get("/admin/ranking.pdf").content)

    assert _without_timestamp(portal_text) == _without_timestamp(admin_text)
    assert "HB9TVK/P" in portal_text


@pytest.mark.django_db
def test_dashboard_offers_the_ranking_once_published(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    client.force_login(p.user)

    before = client.get("/submission/").content.decode()
    assert "/submission/ranking.pdf" not in before

    _publish(seeded_contest)
    after = client.get("/submission/").content.decode()
    assert "/submission/ranking.pdf" in after

"""Ranking PDF (F4 — magazine export).

Service-level: build_ranking_pdf returns a syntactically valid PDF
that contains the participants and the section titles. View-level:
gated on _staff_required and routed to /admin/ranking.pdf.
"""
from __future__ import annotations

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

"""Participant list PDF (M4A.1).

Service-level: build_participant_list_pdf returns a valid PDF that
includes every active participant's callsign and operator name.

View-level: gated on contest.state, served from the portal, the admin
index links into it.
"""
from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from pypdf import PdfReader

from core.models import Contest, Participant, StationDescription
from core.participant_list_pdf import build_participant_list_pdf

User = get_user_model()


def _pdf_text(blob: bytes) -> str:
    """Extract all visible text from a PDF blob (across all pages)."""
    reader = PdfReader(BytesIO(blob))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _make_participant(
    contest, *, username, callsign, first_name=None, modes=3, multi_op=False,
    station_chief="", cancelled=False, location_text=None,
) -> Participant:
    user = User.objects.create_user(username=username, password="x", email=f"{username.lower()}@x.org")
    p = Participant.objects.create(
        contest=contest, user=user, callsign=callsign,
        first_name=first_name or username, email=f"{username.lower()}@x.org",
        coord_system_input="ch1903plus",
        coord_input_e="2681239", coord_input_n="1237065",
        ch1903p_e=2_681_239, ch1903p_n=1_237_065,
        wgs84_lat=46.8, wgs84_lon=8.5,
        altitude_m=1500, canton="ZH", operating_modes=modes,
        multi_op=multi_op, station_chief=station_chief,
        cancelled_at=timezone.now() if cancelled else None,
    )
    if location_text:
        StationDescription.objects.create(participant=p, location_text=location_text)
    return p


# --- service ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_build_pdf_returns_valid_pdf_bytes(seeded_contest):
    _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    blob = build_participant_list_pdf(seeded_contest)
    assert blob.startswith(b"%PDF-")
    assert blob.rstrip().endswith(b"%%EOF")
    assert len(blob) > 1000  # something more than just a header


@pytest.mark.django_db
def test_build_pdf_handles_empty_participant_set(seeded_contest):
    """No registrations yet — must still produce a syntactically valid PDF
    (just header + table header + footer)."""
    blob = build_participant_list_pdf(seeded_contest)
    assert blob.startswith(b"%PDF-")


@pytest.mark.django_db
def test_build_pdf_excludes_cancelled_participants(seeded_contest):
    """Cancelled rows don't belong on the participant list."""
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P")
    _make_participant(seeded_contest, username="HB9B", callsign="HB9B/P", cancelled=True)

    text = _pdf_text(build_participant_list_pdf(seeded_contest))
    assert "HB9A/P" in text
    assert "HB9B/P" not in text


@pytest.mark.django_db
def test_build_pdf_includes_operator_and_callsign(seeded_contest):
    _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P", first_name="Peter")
    text = _pdf_text(build_participant_list_pdf(seeded_contest))
    assert "HB9TVK/P" in text
    assert "Peter" in text


@pytest.mark.django_db
def test_build_pdf_op_column_is_just_first_name(seeded_contest):
    """Even for multi-op stations the Op column is the registered
    participant's first name only; the station chief callsign does not
    appear on the printed roster."""
    _make_participant(
        seeded_contest, username="HB9MO", callsign="HB9MO/P",
        first_name="Anna", multi_op=True, station_chief="HB9CHIEF/P",
    )
    text = _pdf_text(build_participant_list_pdf(seeded_contest))
    assert "Anna" in text
    assert "HB9CHIEF/P" not in text


@pytest.mark.django_db
def test_build_pdf_includes_site_when_station_description_present(seeded_contest):
    _make_participant(
        seeded_contest, username="HB9A", callsign="HB9A/P", location_text="Albispass",
    )
    text = _pdf_text(build_participant_list_pdf(seeded_contest))
    assert "Albispass" in text


@pytest.mark.django_db
def test_build_pdf_sorts_by_callsign(seeded_contest):
    """Two participants in reverse-callsign creation order should appear
    sorted in the rendered PDF (alphabetical)."""
    _make_participant(seeded_contest, username="HB9ZZZ", callsign="HB9ZZZ/P")
    _make_participant(seeded_contest, username="HB9AAA", callsign="HB9AAA/P")
    text = _pdf_text(build_participant_list_pdf(seeded_contest))
    pos_aaa = text.find("HB9AAA/P")
    pos_zzz = text.find("HB9ZZZ/P")
    assert pos_aaa >= 0 and pos_zzz >= 0
    assert pos_aaa < pos_zzz


# --- view: gating ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_participant_list_requires_login(client, seeded_contest):
    response = client.get("/submission/participant-list.pdf")
    assert response.status_code in (301, 302)
    assert "/submission/login/" in response["Location"]


@pytest.mark.django_db
def test_participant_list_blocked_while_registration_open(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    # seeded_contest starts in REGISTRATION_OPEN.
    client.force_login(p.user)
    response = client.get("/submission/participant-list.pdf")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")


@pytest.mark.django_db
def test_participant_list_available_after_registration_closed(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save(update_fields=["state"])

    client.force_login(p.user)
    response = client.get("/submission/participant-list.pdf")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert "attachment" in response["Content-Disposition"]
    assert f"nmd-{seeded_contest.year}-participants.pdf" in response["Content-Disposition"]


@pytest.mark.django_db
def test_participant_list_available_to_staff_even_without_participation(client, seeded_contest):
    """Admin staff aren't registered as Participants but still need the
    download. The view is gated on login + contest state, not on
    being a participant."""
    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save(update_fields=["state"])
    staff = User.objects.create_user(username="STAFF", password="x", email="s@x.org", is_staff=True)
    client.force_login(staff)
    response = client.get("/submission/participant-list.pdf")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")


# --- dashboard link visibility ---------------------------------------------------------------


@pytest.mark.django_db
def test_dashboard_hides_participant_list_link_while_registration_open(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    client.force_login(p.user)
    body = client.get("/submission/").content.decode()
    assert "Participant list" not in body


@pytest.mark.django_db
def test_dashboard_shows_participant_list_link_after_registration_closed(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save(update_fields=["state"])
    client.force_login(p.user)
    body = client.get("/submission/").content.decode()
    assert "Participant list" in body
    assert "/submission/participant-list.pdf" in body

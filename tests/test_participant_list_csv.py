"""Participant list CSV export (F1).

Format expected by dedicated logging software (one row per active
participant)::

    <callsign-lowercase>/p,<first_name>,<east-LV03>,<north-LV03>,,,,

Tests cover the byte-level format, view-level gating (same as the PDF
download), and the admin preview that bypasses the gate.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import Contest, Participant
from core.participant_list_csv import build_participant_list_csv

User = get_user_model()


def _make_participant(
    contest, *, username, callsign, first_name=None,
    cancelled=False, lv95_e=2_681_239, lv95_n=1_237_065,
):
    user = User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org",
    )
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign,
        first_name=first_name or username, email=f"{username.lower()}@x.org",
        coord_system_input="ch1903plus",
        coord_input_e=str(lv95_e), coord_input_n=str(lv95_n),
        ch1903p_e=lv95_e, ch1903p_n=lv95_n,
        wgs84_lat=46.8, wgs84_lon=8.5,
        altitude_m=1500, canton="BE", operating_modes=3,
        cancelled_at=timezone.now() if cancelled else None,
    )


# --- service ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_csv_row_format(seeded_contest):
    """Sample row: hb9tvk/p,Peter,681239,237065,,,,"""
    _make_participant(
        seeded_contest, username="HB9TVK", callsign="HB9TVK",
        first_name="Peter", lv95_e=2_681_239, lv95_n=1_237_065,
    )
    csv_text = build_participant_list_csv(seeded_contest).decode("utf-8")
    lines = csv_text.strip("\n").split("\n")
    assert len(lines) == 1
    assert lines[0] == "hb9tvk/p,Peter,681239,237065,,,,"


@pytest.mark.django_db
def test_csv_orders_rows_by_callsign(seeded_contest):
    _make_participant(seeded_contest, username="HB9ZZZ", callsign="HB9ZZZ")
    _make_participant(seeded_contest, username="HB9AAA", callsign="HB9AAA")
    _make_participant(seeded_contest, username="HB9MMM", callsign="HB9MMM")
    rows = build_participant_list_csv(seeded_contest).decode("utf-8").strip("\n").split("\n")
    assert [r.split(",")[0] for r in rows] == ["hb9aaa/p", "hb9mmm/p", "hb9zzz/p"]


@pytest.mark.django_db
def test_csv_excludes_cancelled_participants(seeded_contest):
    _make_participant(seeded_contest, username="HB9OK", callsign="HB9OK")
    _make_participant(seeded_contest, username="HB9CX", callsign="HB9CX", cancelled=True)
    rows = build_participant_list_csv(seeded_contest).decode("utf-8").strip("\n").split("\n")
    assert rows == ["hb9ok/p,HB9OK,681239,237065,,,,"]


@pytest.mark.django_db
def test_csv_empty_contest_produces_no_rows(seeded_contest):
    assert build_participant_list_csv(seeded_contest) == b""


@pytest.mark.django_db
def test_csv_preserves_utf8_umlauts_in_first_name(seeded_contest):
    _make_participant(
        seeded_contest, username="HB9KOG", callsign="HB9KOG", first_name="Röbi",
    )
    blob = build_participant_list_csv(seeded_contest)
    assert "Röbi".encode("utf-8") in blob


# --- portal view (gated) --------------------------------------------------------------------


@pytest.mark.django_db
def test_portal_csv_requires_login(client, seeded_contest):
    response = client.get("/submission/participant-list.csv")
    assert response.status_code in (301, 302)
    assert "/submission/login/" in response["Location"]


@pytest.mark.django_db
def test_portal_csv_blocked_while_registration_open(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK")
    client.force_login(p.user)
    response = client.get("/submission/participant-list.csv")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")


@pytest.mark.django_db
def test_portal_csv_available_after_registration_closed(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK")
    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save(update_fields=["state"])
    client.force_login(p.user)
    response = client.get("/submission/participant-list.csv")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/plain; charset=utf-8"
    expected_name = f"NMD_Stn{seeded_contest.year % 100:02d}.txt"
    assert expected_name in response["Content-Disposition"]
    assert b"hb9tvk/p" in response.content


# --- admin preview (no gate) -----------------------------------------------------------------


@pytest.mark.django_db
def test_admin_csv_preview_works_in_any_state(client, seeded_contest):
    assert seeded_contest.state == Contest.State.REGISTRATION_OPEN
    _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK")
    staff = User.objects.create_user(username="STAFF", password="x", email="s@x.org", is_staff=True)
    client.force_login(staff)
    response = client.get("/admin/participant-list-preview.csv")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/plain; charset=utf-8"
    expected_name = f"NMD_Stn{seeded_contest.year % 100:02d}.txt"
    assert expected_name in response["Content-Disposition"]
    assert b"hb9tvk/p" in response.content


@pytest.mark.django_db
def test_admin_csv_preview_requires_staff(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK")
    client.force_login(p.user)
    response = client.get("/admin/participant-list-preview.csv")
    assert response.status_code in (301, 302, 403)

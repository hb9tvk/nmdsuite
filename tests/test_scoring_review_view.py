"""Staff-only scoring review page — smoke tests."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model

from core.models import Participant, QsoEntry
from scoring.pairing import score_contest

User = get_user_model()


def _make_staff_user():
    return User.objects.create_user(
        username="STAFF", password="x", email="staff@x.org", is_staff=True,
    )


def _make_participant(contest, *, username, callsign):
    user = User.objects.create_user(username=username, password="x", email=f"{username.lower()}@x.org")
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign, first_name=username,
        email=f"{username.lower()}@x.org", coord_system_input="wgs84",
        coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2, ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", operating_modes=3,
    )


def _qso(p, *, t, remote_call, mode="CW", rsts="599", rstr="599", txts="", txtr=""):
    return QsoEntry.objects.create(
        participant=p, utc_raw=t.strftime("%H%M"), utc_time=t, mode=mode,
        remote_call=remote_call, rsts=rsts, rstr=rstr, txts=txts, txtr=txtr,
    )


TXT_A = "HB9TVK PIZ KESCH 3418M"
TXT_B = "HB9ABC ALPSTEIN 2502M"


@pytest.mark.django_db
def test_review_index_requires_login(client):
    response = client.get("/scoring/")
    assert response.status_code in (301, 302)


@pytest.mark.django_db
def test_review_index_redirects_non_staff(client, seeded_contest):
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.get("/scoring/")
    # user_passes_test redirects to login (302) when the predicate fails.
    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_review_index_renders_for_staff_no_participants(client, seeded_contest):
    client.force_login(_make_staff_user())
    response = client.get("/scoring/")
    assert response.status_code == 200
    assert b"import a legacy DB" in response.content


@pytest.mark.django_db
def test_review_index_lists_participants_with_totals(client, seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    score_contest(seeded_contest)

    client.force_login(_make_staff_user())
    response = client.get("/scoring/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "HB9TVK/P" in body
    assert "HB9ABC/P" in body
    # Each participant earned 4 points from the full-match pair.
    assert "<strong>4</strong>" in body


@pytest.mark.django_db
def test_review_participant_shows_qso_log_with_statuses(client, seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    _qso(a, t=t + timedelta(minutes=10), remote_call="DL1XYZ", mode="SSB", rsts="59", rstr="59")
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    score_contest(seeded_contest)

    client.force_login(_make_staff_user())
    response = client.get(f"/scoring/{a.pk}/")
    assert response.status_code == 200
    body = response.content.decode()
    # Both QSO rows surface their status badges.
    assert "badge-full_match" in body
    assert "badge-dx_qso" in body
    # The matched peer's QSO appears inline.
    assert "paired:" in body


@pytest.mark.django_db
def test_review_unknown_participant_404s(client, seeded_contest):
    client.force_login(_make_staff_user())
    response = client.get("/scoring/99999/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_review_year_switcher_renders_all_contest_years(client, seeded_contest):
    """The sidebar's year switcher should list every Contest row, including archived."""
    from datetime import date, datetime, time, timezone
    from core.models import Contest

    Contest.objects.create(
        year=2025, contest_date=date(2025, 7, 20),
        start_utc=datetime(2025, 7, 20, 6, tzinfo=timezone.utc),
        half_split_utc=datetime(2025, 7, 20, 8, tzinfo=timezone.utc),
        end_utc=datetime(2025, 7, 20, 9, 59, 59, tzinfo=timezone.utc),
        state=Contest.State.ARCHIVED,
    )
    client.force_login(_make_staff_user())
    response = client.get("/scoring/")
    body = response.content.decode()
    assert "year-link" in body
    assert ">2026<" in body
    assert ">2025<" in body  # archived contest still listed in the switcher


@pytest.mark.django_db
def test_review_year_param_selects_a_specific_contest(client, seeded_contest):
    from datetime import date, datetime, time, timezone
    from core.models import Contest

    other = Contest.objects.create(
        year=2025, contest_date=date(2025, 7, 20),
        start_utc=datetime(2025, 7, 20, 6, tzinfo=timezone.utc),
        half_split_utc=datetime(2025, 7, 20, 8, tzinfo=timezone.utc),
        end_utc=datetime(2025, 7, 20, 9, 59, 59, tzinfo=timezone.utc),
        state=Contest.State.ARCHIVED,
    )
    _make_participant(other, username="HB9OLD", callsign="HB9OLD/P")
    _make_participant(seeded_contest, username="HB9NEW", callsign="HB9NEW/P")

    client.force_login(_make_staff_user())

    # Default → 2026 participant.
    body = client.get("/scoring/").content.decode()
    assert "HB9NEW/P" in body
    assert "HB9OLD/P" not in body

    # Explicit ?year=2025 → 2025 participant.
    body = client.get("/scoring/?year=2025").content.decode()
    assert "HB9OLD/P" in body
    assert "HB9NEW/P" not in body


@pytest.mark.django_db
def test_review_handles_unscored_qsos_without_crashing(client, seeded_contest):
    """QSOs missing utc_time/mode/remote_call have no ScoringRecord. The page must still render."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    QsoEntry.objects.create(
        participant=a, utc_raw="bad", utc_time=None, mode="",
        remote_call="", rsts="", rstr="",
    )
    score_contest(seeded_contest)

    client.force_login(_make_staff_user())
    response = client.get(f"/scoring/{a.pk}/")
    assert response.status_code == 200
    assert b"badge-none" in response.content  # the "not scored" badge

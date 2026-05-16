"""Portal scoring view (M2.6).

Per-participant scoring breakdown + QSO log. Gated on
``contest.results_published_at`` (set by M4.2's publish_results
transition).
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import (
    Contest, Participant, QsoEntry, ScoringRecord, ScoringStatus,
)

User = get_user_model()


@pytest.fixture
def registered_user(seeded_contest):
    user = User.objects.create_user(
        username="HB9TVK", password="x", email="t@example.org", first_name="Peter",
    )
    p = Participant.objects.create(
        contest=seeded_contest, user=user, callsign="HB9TVK/P",
        first_name="Peter", email="t@example.org",
        coord_system_input="wgs84", coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2, ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", operating_modes=3,
    )
    return user, p


def _publish(contest: Contest) -> None:
    contest.results_published_at = timezone.now()
    contest.state = Contest.State.PUBLISHED
    contest.save(update_fields=["results_published_at", "state"])


def _add_qso(p: Participant, *, t_offset_min: int, mode: str, remote_call: str) -> QsoEntry:
    """Add a QSO at `t_offset_min` minutes after contest start."""
    from datetime import timedelta
    rsts = "599" if mode == "CW" else "59"
    return QsoEntry.objects.create(
        participant=p,
        utc_raw=(p.contest.start_utc + timedelta(minutes=t_offset_min)).strftime("%H%M"),
        utc_time=p.contest.start_utc + timedelta(minutes=t_offset_min),
        mode=mode, remote_call=remote_call, rsts=rsts, rstr=rsts,
        txts="x", txtr="x",
    )


def _score(qso: QsoEntry, *, status: ScoringStatus, points: int, half: int) -> ScoringRecord:
    return ScoringRecord.objects.create(
        qso=qso, status=status, points=points, half=half,
    )


# --- access control --------------------------------------------------------------------------


@pytest.mark.django_db
def test_scoring_requires_login(client):
    response = client.get("/submission/scoring/")
    assert response.status_code in (301, 302)
    assert "/submission/login/" in response["Location"]


@pytest.mark.django_db
def test_scoring_redirects_unregistered_user(client, seeded_contest):
    """A logged-in user without a participation in the active contest should
    be bounced back to the dashboard, not see another operator's scoring."""
    user = User.objects.create_user(username="HB9NOREG", password="x", email="n@x.org")
    client.force_login(user)
    response = client.get("/submission/scoring/")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")


# --- gating on results_published_at ----------------------------------------------------------


@pytest.mark.django_db
def test_scoring_unpublished_shows_holding_message(client, registered_user):
    user, p = registered_user
    client.force_login(user)
    response = client.get("/submission/scoring/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "have not been published yet" in body
    # No breakdown table when unpublished.
    assert "scoring-breakdown" not in body
    assert "scoring-qso-table" not in body


@pytest.mark.django_db
def test_scoring_published_renders_breakdown(client, registered_user):
    user, p = registered_user
    # CW H1: 2 QSOs × 3 pts = 6. CW H2: 1 × 3 = 3. SSB H1: 1 × 2 = 2.
    q1 = _add_qso(p, t_offset_min=10, mode="CW", remote_call="HB9A/P")
    q2 = _add_qso(p, t_offset_min=20, mode="CW", remote_call="HB9B/P")
    q3 = _add_qso(p, t_offset_min=130, mode="CW", remote_call="HB9C/P")  # > 2h = H2
    q4 = _add_qso(p, t_offset_min=15, mode="SSB", remote_call="HB9D/P")
    _score(q1, status=ScoringStatus.FULL_MATCH, points=3, half=1)
    _score(q2, status=ScoringStatus.FULL_MATCH, points=3, half=1)
    _score(q3, status=ScoringStatus.FULL_MATCH, points=3, half=2)
    _score(q4, status=ScoringStatus.FULL_MATCH, points=2, half=1)

    _publish(p.contest)
    client.force_login(user)

    response = client.get("/submission/scoring/")
    assert response.status_code == 200
    body = response.content.decode()
    # Breakdown numbers are present.
    assert "scoring-breakdown" in body
    # CW total = 9, SSB total = 2, combined = 11.
    assert ">9<" in body  # CW total cell
    assert ">2<" in body  # SSB total cell
    assert ">11<" in body  # combined total


@pytest.mark.django_db
def test_scoring_published_renders_qso_rows_with_badges(client, registered_user):
    user, p = registered_user
    q = _add_qso(p, t_offset_min=10, mode="CW", remote_call="HB9A/P")
    _score(q, status=ScoringStatus.FULL_MATCH, points=3, half=1)
    _publish(p.contest)
    client.force_login(user)

    body = client.get("/submission/scoring/").content.decode()
    assert "scoring-qso-table" in body
    assert "HB9A/P" in body
    assert "badge-full_match" in body


@pytest.mark.django_db
def test_scoring_published_no_qsos_shows_empty_message(client, registered_user):
    user, p = registered_user
    _publish(p.contest)
    client.force_login(user)

    body = client.get("/submission/scoring/").content.decode()
    assert "No QSOs logged" in body


# --- dashboard link --------------------------------------------------------------------------


@pytest.mark.django_db
def test_dashboard_hides_scoring_link_when_unpublished(client, registered_user):
    user, p = registered_user
    client.force_login(user)
    body = client.get("/submission/").content.decode()
    assert "View your scoring" not in body


@pytest.mark.django_db
def test_dashboard_shows_scoring_link_when_published(client, registered_user):
    user, p = registered_user
    _publish(p.contest)
    client.force_login(user)
    body = client.get("/submission/").content.decode()
    assert "View your scoring" in body
    assert "/submission/scoring/" in body


# --- cancelled-participant edge case ---------------------------------------------------------


@pytest.mark.django_db
def test_scoring_redirects_cancelled_participant(client, registered_user):
    user, p = registered_user
    p.cancelled_at = timezone.now()
    p.save(update_fields=["cancelled_at"])
    _publish(p.contest)
    client.force_login(user)

    response = client.get("/submission/scoring/")
    # _active_participation filters out cancelled rows, so this user is
    # treated as 'not registered' and bounced to the dashboard.
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")

"""Fixstation Review admin surface (M4B)."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from admin_module.fixstation_service import build_candidates
from core.models import AuditLog, InvalidCallsign, Participant, QsoEntry, ScoringRecord, ScoringStatus

User = get_user_model()


def _make_participant(contest, *, username, callsign, submitted=True):
    user = User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org",
    )
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign, first_name=username,
        email=f"{username.lower()}@x.org", coord_system_input="wgs84",
        coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2, ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", location_text="Niederhorn", operating_modes=3,
        submitted_at=timezone.now() if submitted else None,
    )


def _add_qso(p, *, remote, mode="CW"):
    return QsoEntry.objects.create(
        participant=p, utc_raw="0700", utc_time=p.contest.start_utc, mode=mode,
        remote_call=remote, rsts="599", rstr="599",
    )


def _staff(username="STAFF"):
    return User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org", is_staff=True,
    )


# --- service: build_candidates ---------------------------------------------------------------


@pytest.mark.django_db
def test_candidates_lists_only_callsigns_with_1_or_2_loggers(seeded_contest):
    """Non-NMD remotes logged by 3+ stations are corroborated and not shown."""
    a = _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P")
    b = _make_participant(seeded_contest, username="HB9B", callsign="HB9B/P")
    c = _make_participant(seeded_contest, username="HB9C", callsign="HB9C/P")
    # DL1ABC logged by 1 → suspicious
    _add_qso(a, remote="DL1ABC")
    # DL1DEF logged by 2 → still suspicious
    _add_qso(a, remote="DL1DEF")
    _add_qso(b, remote="DL1DEF")
    # DL1GHI logged by 3 → corroborated, hidden
    _add_qso(a, remote="DL1GHI")
    _add_qso(b, remote="DL1GHI")
    _add_qso(c, remote="DL1GHI")

    candidates = build_candidates(seeded_contest)
    by_call = {c.callsign: c for c in candidates}
    assert set(by_call) == {"DL1ABC", "DL1DEF"}
    assert by_call["DL1ABC"].logger_count == 1
    assert by_call["DL1DEF"].logger_count == 2


@pytest.mark.django_db
def test_candidates_normalise_to_core_callsign(seeded_contest):
    """``F/DL1ABC/P`` and ``DL1ABC`` should group under one candidate."""
    a = _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P")
    b = _make_participant(seeded_contest, username="HB9B", callsign="HB9B/P")
    _add_qso(a, remote="F/DL1ABC/P")
    _add_qso(b, remote="DL1ABC")

    candidates = build_candidates(seeded_contest)
    assert [c.callsign for c in candidates] == ["DL1ABC"]
    assert candidates[0].logger_count == 2


@pytest.mark.django_db
def test_candidates_exclude_nmd_registered_callsigns(seeded_contest):
    """An NMD-registered callsign that someone logged isn't a candidate
    even if it appears in only one log — it's not 'non-NMD'."""
    a = _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P")
    _make_participant(seeded_contest, username="HB9B", callsign="HB9B/P")
    _add_qso(a, remote="HB9B/P")  # NMD station — corroborated by its own log

    assert build_candidates(seeded_contest) == []


@pytest.mark.django_db
def test_candidates_skip_unsubmitted_participants(seeded_contest):
    """Drafts (no submitted_at) don't count toward logger count."""
    a = _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P")
    _make_participant(seeded_contest, username="HB9B", callsign="HB9B/P", submitted=False)
    _add_qso(a, remote="DL1ABC")
    # b's log is not yet submitted, so they don't count.
    # But we still need at least 1 logger to surface the call.
    candidates = build_candidates(seeded_contest)
    assert [c.callsign for c in candidates] == ["DL1ABC"]
    assert candidates[0].logger_count == 1


@pytest.mark.django_db
def test_candidates_flag_existing_invalid_marks(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P")
    _add_qso(a, remote="DL1ABC")
    InvalidCallsign.objects.create(contest=seeded_contest, callsign="DL1ABC")

    candidates = build_candidates(seeded_contest)
    assert candidates[0].is_invalid is True


# --- view ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_view_requires_staff(client, seeded_contest):
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.get("/admin/fixstation/")
    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_view_renders_candidates_with_lookup_links(client, seeded_contest):
    a = _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P")
    _add_qso(a, remote="DL1ABC")

    client.force_login(_staff())
    response = client.get("/admin/fixstation/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "DL1ABC" in body
    # All three external lookups present with the normalised callsign.
    assert "qrz.com/db/DL1ABC" in body
    assert "qrzcq.com/call/DL1ABC" in body
    assert "hamqth.com/DL1ABC" in body


@pytest.mark.django_db
def test_post_marks_callsigns_invalid_and_reruns_scoring(client, seeded_contest):
    """Ticking a callsign saves an InvalidCallsign row and re-runs scoring
    so the affected QSOs immediately score 0 pt."""
    a = _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P")
    _add_qso(a, remote="DL1ABC")
    staff = _staff()
    client.force_login(staff)

    response = client.post("/admin/fixstation/", {"invalid": ["DL1ABC"]})
    assert response.status_code == 302

    assert InvalidCallsign.objects.filter(
        contest=seeded_contest, callsign="DL1ABC",
    ).exists()
    # Re-score ran: the QSO is now INVALID_CALL with 0 pt.
    rec = ScoringRecord.objects.get(qso__participant=a)
    assert rec.status == ScoringStatus.INVALID_CALL
    assert rec.points == 0
    # Audit row exists with admin actor.
    audit = AuditLog.objects.get(action="fixstation.update")
    assert audit.actor == staff
    assert audit.payload["added"] == 1


@pytest.mark.django_db
def test_post_unticking_removes_the_flag_and_restores_points(client, seeded_contest):
    a = _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P")
    _add_qso(a, remote="DL1ABC")
    InvalidCallsign.objects.create(contest=seeded_contest, callsign="DL1ABC")

    client.force_login(_staff())
    # Empty POST means everything unticked.
    response = client.post("/admin/fixstation/", {})
    assert response.status_code == 302

    assert not InvalidCallsign.objects.filter(contest=seeded_contest).exists()
    rec = ScoringRecord.objects.get(qso__participant=a)
    # Re-scored without the flag → back to DX_QSO at 1 pt.
    assert rec.status == ScoringStatus.DX_QSO
    assert rec.points == 1


@pytest.mark.django_db
def test_post_normalises_callsign_before_saving(client, seeded_contest):
    a = _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P")
    _add_qso(a, remote="DL1ABC")
    client.force_login(_staff())

    # Lowercase input — view should uppercase before saving.
    client.post("/admin/fixstation/", {"invalid": ["dl1abc"]})
    assert InvalidCallsign.objects.filter(
        contest=seeded_contest, callsign="DL1ABC",
    ).exists()


@pytest.mark.django_db
def test_post_with_no_changes_does_not_audit(client, seeded_contest):
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P")
    client.force_login(_staff())
    client.post("/admin/fixstation/", {})
    assert not AuditLog.objects.filter(action="fixstation.update").exists()

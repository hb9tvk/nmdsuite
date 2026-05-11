"""M3.5 — points assignment + per-participant breakdown."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model

from core.models import Participant, QsoEntry, ScoringRecord, ScoringStatus
from scoring.pairing import score_contest
from scoring.points import POINTS_BY_STATUS, points_for
from scoring.totals import ModeBreakdown, participant_breakdown

User = get_user_model()


def _make_participant(contest, *, username, callsign):
    user = User.objects.create_user(username=username, password="x", email=f"{username.lower()}@x.org")
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign, first_name="X",
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


# --- points_for ------------------------------------------------------------------------------


def test_points_table_per_status():
    assert points_for(ScoringStatus.FULL_MATCH) == 4
    assert points_for(ScoringStatus.ADMIN_ACCEPTED) == 4
    assert points_for(ScoringStatus.HB9_QSO) == 1
    assert points_for(ScoringStatus.DX_QSO) == 1
    assert points_for(ScoringStatus.TEXT_MISMATCH) == 0
    assert points_for(ScoringStatus.UNMATCHED) == 0
    assert points_for(ScoringStatus.SUSPECTED_CALL_MISMATCH) == 0
    assert points_for(ScoringStatus.DUPE_DEDUCTED) == 0


def test_points_table_covers_every_status():
    """If a new status is added without updating POINTS_BY_STATUS we want to know."""
    declared = {s.value for s in ScoringStatus}
    mapped = {s if isinstance(s, str) else s.value for s in POINTS_BY_STATUS.keys()}
    assert declared == mapped, f"missing or extra statuses: {declared ^ mapped}"


# --- score_contest writes points on records --------------------------------------------------


@pytest.mark.django_db
def test_score_contest_writes_points_per_status(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    full_a = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    hb9 = _qso(a, t=t + timedelta(minutes=10), remote_call="HB9NON")
    dx = _qso(a, t=t + timedelta(minutes=20), remote_call="DL1ABC")
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)

    score_contest(seeded_contest)
    assert ScoringRecord.objects.get(qso=full_a).points == 4
    assert ScoringRecord.objects.get(qso=hb9).points == 1
    assert ScoringRecord.objects.get(qso=dx).points == 1


@pytest.mark.django_db
def test_score_contest_zeroes_points_for_zero_statuses(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")  # silent
    t = seeded_contest.start_utc
    un = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    score_contest(seeded_contest)
    assert ScoringRecord.objects.get(qso=un).points == 0


# --- participant_breakdown -------------------------------------------------------------------


@pytest.mark.django_db
def test_participant_breakdown_splits_cw_ssb_h1_h2(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    h1 = seeded_contest.start_utc
    h2 = seeded_contest.half_split_utc + timedelta(minutes=10)
    # 2 HB9 in CW H1, 1 HB9 in CW H2, 1 DX in SSB H2.
    _qso(a, t=h1, mode="CW", remote_call="HB9NON1")
    _qso(a, t=h1 + timedelta(minutes=5), mode="CW", remote_call="HB9NON2")
    _qso(a, t=h2, mode="CW", remote_call="HB9NON3")
    _qso(a, t=h2 + timedelta(minutes=5), mode="SSB", rsts="59", rstr="59", remote_call="DL1ABC")

    score_contest(seeded_contest)
    b = participant_breakdown(a)
    assert b.cw == ModeBreakdown(h1=2, h2=1)
    assert b.ssb == ModeBreakdown(h1=0, h2=1)
    assert b.cw.total == 3
    assert b.ssb.total == 1
    assert b.total == 4


@pytest.mark.django_db
def test_participant_breakdown_excludes_dupes(seeded_contest):
    """DUPE_DEDUCTED rows have points=0 so they fall out of the sum naturally."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    _qso(a, t=t + timedelta(minutes=10), remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)  # dupe
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    _qso(b, t=t + timedelta(minutes=10), remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)

    score_contest(seeded_contest)
    # A had one valid full match (4 pts) + one dupe (0). Should be 4 in CW H1.
    breakdown = participant_breakdown(a)
    assert breakdown.cw.h1 == 4
    assert breakdown.total == 4


@pytest.mark.django_db
def test_participant_breakdown_empty_log_returns_zeros(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = participant_breakdown(a)
    assert b.cw.total == 0
    assert b.ssb.total == 0
    assert b.total == 0

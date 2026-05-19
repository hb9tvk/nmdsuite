"""Scoring engine — M4B INVALID_CALL classification.

When a non-NMD remote callsign is flagged via ``InvalidCallsign``, every
QSO whose ``core_callsign(remote_call)`` matches that flag downgrades
from ``HB9_QSO`` / ``DX_QSO`` (1 pt) to ``INVALID_CALL`` (0 pt).
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from core.models import (
    InvalidCallsign,
    Participant,
    QsoEntry,
    ScoringRecord,
    ScoringStatus,
)
from scoring.pairing import score_contest

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


def _add_qso(p, *, mode="CW", remote, utc_offset_min=0):
    return QsoEntry.objects.create(
        participant=p,
        utc_raw=f"070{utc_offset_min}",
        utc_time=p.contest.start_utc.replace(minute=utc_offset_min),
        mode=mode,
        remote_call=remote,
        rsts="599", rstr="599",
    )


@pytest.mark.django_db
def test_invalid_callsign_downgrades_hb9_qso(seeded_contest):
    """A Swiss non-NMD remote that's flagged invalid scores as
    INVALID_CALL with 0 points."""
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _add_qso(p, remote="HB9FAKE")  # would normally be HB9_QSO at 1 pt
    InvalidCallsign.objects.create(contest=seeded_contest, callsign="HB9FAKE")

    score_contest(seeded_contest)
    rec = ScoringRecord.objects.get(qso__participant=p)
    assert rec.status == ScoringStatus.INVALID_CALL
    assert rec.points == 0


@pytest.mark.django_db
def test_invalid_callsign_downgrades_dx_qso(seeded_contest):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _add_qso(p, remote="DL1FAKE")  # DX_QSO at 1 pt
    InvalidCallsign.objects.create(contest=seeded_contest, callsign="DL1FAKE")

    score_contest(seeded_contest)
    rec = ScoringRecord.objects.get(qso__participant=p)
    assert rec.status == ScoringStatus.INVALID_CALL
    assert rec.points == 0


@pytest.mark.django_db
def test_invalid_callsign_does_not_touch_nmd_qsos(seeded_contest):
    """An NMD↔NMD QSO must never become INVALID_CALL — only HB9_QSO
    and DX_QSO are candidates."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    _add_qso(a, remote="HB9ABC/P")
    _add_qso(b, remote="HB9TVK/P")
    # Even if someone (mistakenly?) flagged the NMD callsign — should be ignored.
    InvalidCallsign.objects.create(contest=seeded_contest, callsign="HB9ABC")

    score_contest(seeded_contest)
    rec_a = ScoringRecord.objects.get(qso__participant=a)
    # NMD↔NMD pairing stays intact.
    assert rec_a.status != ScoringStatus.INVALID_CALL


@pytest.mark.django_db
def test_invalid_callsign_normalised_to_core(seeded_contest):
    """The flag is stored as the core callsign (no /P, no F/). A QSO
    against ``F/DL1ABC/P`` should match a flag of ``DL1ABC``."""
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _add_qso(p, remote="F/DL1ABC/P")
    InvalidCallsign.objects.create(contest=seeded_contest, callsign="DL1ABC")

    score_contest(seeded_contest)
    rec = ScoringRecord.objects.get(qso__participant=p)
    assert rec.status == ScoringStatus.INVALID_CALL


@pytest.mark.django_db
def test_unflagging_restores_hb9_or_dx_qso(seeded_contest):
    """After removing the InvalidCallsign row, re-scoring restores
    HB9_QSO/DX_QSO + 1 pt."""
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _add_qso(p, remote="DL1FAKE")
    flag = InvalidCallsign.objects.create(contest=seeded_contest, callsign="DL1FAKE")
    score_contest(seeded_contest)
    assert ScoringRecord.objects.get(qso__participant=p).status == ScoringStatus.INVALID_CALL

    flag.delete()
    score_contest(seeded_contest)
    rec = ScoringRecord.objects.get(qso__participant=p)
    assert rec.status == ScoringStatus.DX_QSO
    assert rec.points == 1

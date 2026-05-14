"""M3.3 — dupe deduction by best-quality per (peer, mode, half)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model

from core.models import Participant, QsoEntry, ScoringRecord, ScoringStatus
from scoring.dupes import mark_dupes
from scoring.pairing import _qso_half, score_contest

User = get_user_model()


# --- fixtures --------------------------------------------------------------------------------


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


def _record(qso, contest, *, status, matched_qso=None, text_distance=0):
    """Build a ScoringRecord in memory (unsaved) — mirrors what score_contest does."""
    return ScoringRecord(
        qso=qso,
        status=status,
        matched_qso=matched_qso,
        text_distance=text_distance,
        half=_qso_half(qso, contest),
    )


TXT_A = "HB9TVK PIZ KESCH 3418M"
TXT_B = "HB9ABC ALPSTEIN 2502M"


# --- mark_dupes (pure logic on in-memory records) --------------------------------------------


@pytest.mark.django_db
def test_mark_dupes_keeps_one_full_match_per_bucket(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    q1 = _qso(a, t=t, remote_call="HB9ABC/P")
    q2 = _qso(a, t=t + timedelta(minutes=20), remote_call="HB9ABC/P")
    rs = [
        _record(q1, seeded_contest, status=ScoringStatus.FULL_MATCH),
        _record(q2, seeded_contest, status=ScoringStatus.FULL_MATCH),
    ]
    flipped = mark_dupes(rs)
    assert flipped == 1
    assert rs[0].status == ScoringStatus.FULL_MATCH
    assert rs[1].status == ScoringStatus.DUPE_DEDUCTED


@pytest.mark.django_db
def test_mark_dupes_prefers_full_over_text_mismatch(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    # text-mismatch logged FIRST in time, full-match SECOND — full still wins (best-quality, not earliest).
    q_tm = _qso(a, t=t, remote_call="HB9ABC/P")
    q_full = _qso(a, t=t + timedelta(minutes=10), remote_call="HB9ABC/P")
    rs = [
        _record(q_tm, seeded_contest, status=ScoringStatus.TEXT_MISMATCH, text_distance=5),
        _record(q_full, seeded_contest, status=ScoringStatus.FULL_MATCH),
    ]
    mark_dupes(rs)
    # The full-match row keeps its status; the text-mismatch row becomes DUPE_DEDUCTED.
    full_row = next(r for r in rs if r.qso == q_full)
    tm_row = next(r for r in rs if r.qso == q_tm)
    assert full_row.status == ScoringStatus.FULL_MATCH
    assert tm_row.status == ScoringStatus.DUPE_DEDUCTED


@pytest.mark.django_db
def test_mark_dupes_prefers_text_mismatch_over_unmatched(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    q_tm = _qso(a, t=t, remote_call="HB9ABC/P")
    q_un = _qso(a, t=t + timedelta(minutes=5), remote_call="HB9ABC/P")
    rs = [
        _record(q_tm, seeded_contest, status=ScoringStatus.TEXT_MISMATCH, text_distance=5),
        _record(q_un, seeded_contest, status=ScoringStatus.UNMATCHED),
    ]
    mark_dupes(rs)
    tm_row = next(r for r in rs if r.qso == q_tm)
    un_row = next(r for r in rs if r.qso == q_un)
    assert tm_row.status == ScoringStatus.TEXT_MISMATCH
    assert un_row.status == ScoringStatus.DUPE_DEDUCTED


@pytest.mark.django_db
def test_mark_dupes_tie_break_earliest_utc_wins_within_same_status(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    later = _qso(a, t=t + timedelta(minutes=20), remote_call="HB9ABC/P")
    earlier = _qso(a, t=t, remote_call="HB9ABC/P")
    rs = [
        _record(later, seeded_contest, status=ScoringStatus.FULL_MATCH),
        _record(earlier, seeded_contest, status=ScoringStatus.FULL_MATCH),
    ]
    mark_dupes(rs)
    earlier_row = next(r for r in rs if r.qso == earlier)
    later_row = next(r for r in rs if r.qso == later)
    assert earlier_row.status == ScoringStatus.FULL_MATCH
    assert later_row.status == ScoringStatus.DUPE_DEDUCTED


@pytest.mark.django_db
def test_mark_dupes_different_modes_are_not_dupes(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    q_cw = _qso(a, t=t, remote_call="HB9ABC/P", mode="CW")
    q_ssb = _qso(a, t=t + timedelta(minutes=5), remote_call="HB9ABC/P", mode="SSB")
    rs = [
        _record(q_cw, seeded_contest, status=ScoringStatus.FULL_MATCH),
        _record(q_ssb, seeded_contest, status=ScoringStatus.FULL_MATCH),
    ]
    flipped = mark_dupes(rs)
    assert flipped == 0
    assert all(r.status == ScoringStatus.FULL_MATCH for r in rs)


@pytest.mark.django_db
def test_mark_dupes_different_halves_are_not_dupes(seeded_contest):
    """Zweitverbindung: NMD stations may work each other once per mode per half,
    so the *same* (peer, mode) pair across H1 and H2 is allowed."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    h1 = seeded_contest.start_utc
    h2 = seeded_contest.half_split_utc + timedelta(minutes=1)
    q_h1 = _qso(a, t=h1, remote_call="HB9ABC/P")
    q_h2 = _qso(a, t=h2, remote_call="HB9ABC/P")
    rs = [
        _record(q_h1, seeded_contest, status=ScoringStatus.FULL_MATCH),
        _record(q_h2, seeded_contest, status=ScoringStatus.FULL_MATCH),
    ]
    assert rs[0].half == 1 and rs[1].half == 2
    flipped = mark_dupes(rs)
    assert flipped == 0


@pytest.mark.django_db
def test_mark_dupes_portable_suffix_does_not_split_buckets(seeded_contest):
    """``HB9ABC`` and ``HB9ABC/P`` are the same peer — must dupe-deduct."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    q1 = _qso(a, t=t, remote_call="HB9ABC")
    q2 = _qso(a, t=t + timedelta(minutes=10), remote_call="HB9ABC/P")
    rs = [
        _record(q1, seeded_contest, status=ScoringStatus.FULL_MATCH),
        _record(q2, seeded_contest, status=ScoringStatus.FULL_MATCH),
    ]
    flipped = mark_dupes(rs)
    assert flipped == 1


@pytest.mark.django_db
def test_mark_dupes_hb9_same_peer_same_mode_is_a_dupe(seeded_contest):
    """Non-NMD rule: once per (peer, mode) for the whole contest. Second
    HB9 QSO with the same peer in the same mode is a dupe."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    q_first = _qso(a, t=t, remote_call="HB9NON")
    q_dupe = _qso(a, t=t + timedelta(minutes=20), remote_call="HB9NON")
    rs = [
        _record(q_first, seeded_contest, status=ScoringStatus.HB9_QSO),
        _record(q_dupe, seeded_contest, status=ScoringStatus.HB9_QSO),
    ]
    flipped = mark_dupes(rs)
    assert flipped == 1
    assert next(r for r in rs if r.qso == q_first).status == ScoringStatus.HB9_QSO
    assert next(r for r in rs if r.qso == q_dupe).status == ScoringStatus.DUPE_DEDUCTED


@pytest.mark.django_db
def test_mark_dupes_dx_same_peer_same_mode_is_a_dupe(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    q_first = _qso(a, t=t, remote_call="DL1ABC")
    q_dupe = _qso(a, t=t + timedelta(minutes=20), remote_call="DL1ABC")
    rs = [
        _record(q_first, seeded_contest, status=ScoringStatus.DX_QSO),
        _record(q_dupe, seeded_contest, status=ScoringStatus.DX_QSO),
    ]
    flipped = mark_dupes(rs)
    assert flipped == 1
    assert next(r for r in rs if r.qso == q_dupe).status == ScoringStatus.DUPE_DEDUCTED


@pytest.mark.django_db
def test_mark_dupes_non_nmd_different_modes_are_not_dupes(seeded_contest):
    """Once per (peer, mode) — so the same call in CW and SSB is fine."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    q_cw = _qso(a, t=t, mode="CW", rsts="599", rstr="599", remote_call="HB9NON")
    q_ssb = _qso(a, t=t + timedelta(minutes=5), mode="SSB", rsts="59", rstr="59", remote_call="HB9NON")
    rs = [
        _record(q_cw, seeded_contest, status=ScoringStatus.HB9_QSO),
        _record(q_ssb, seeded_contest, status=ScoringStatus.HB9_QSO),
    ]
    flipped = mark_dupes(rs)
    assert flipped == 0


@pytest.mark.django_db
def test_mark_dupes_non_nmd_across_halves_is_still_a_dupe(seeded_contest):
    """Unlike NMD↔NMD, non-NMD has no half-split exception. Two HB9 QSOs
    with the same peer in the same mode across H1 and H2 → second is a dupe."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    h1 = seeded_contest.start_utc
    h2 = seeded_contest.half_split_utc + timedelta(minutes=1)
    q_h1 = _qso(a, t=h1, remote_call="HB9NON")
    q_h2 = _qso(a, t=h2, remote_call="HB9NON")
    rs = [
        _record(q_h1, seeded_contest, status=ScoringStatus.HB9_QSO),
        _record(q_h2, seeded_contest, status=ScoringStatus.HB9_QSO),
    ]
    assert rs[0].half == 1 and rs[1].half == 2
    flipped = mark_dupes(rs)
    assert flipped == 1
    assert next(r for r in rs if r.qso == q_h1).status == ScoringStatus.HB9_QSO
    assert next(r for r in rs if r.qso == q_h2).status == ScoringStatus.DUPE_DEDUCTED


@pytest.mark.django_db
def test_mark_dupes_non_nmd_earliest_utc_wins(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    later = _qso(a, t=t + timedelta(minutes=30), remote_call="HB9NON")
    earlier = _qso(a, t=t, remote_call="HB9NON")
    rs = [
        _record(later, seeded_contest, status=ScoringStatus.HB9_QSO),
        _record(earlier, seeded_contest, status=ScoringStatus.HB9_QSO),
    ]
    mark_dupes(rs)
    assert next(r for r in rs if r.qso == earlier).status == ScoringStatus.HB9_QSO
    assert next(r for r in rs if r.qso == later).status == ScoringStatus.DUPE_DEDUCTED


@pytest.mark.django_db
def test_mark_dupes_non_nmd_portable_suffix_does_not_split_buckets(seeded_contest):
    """HB9NON and HB9NON/P collapse to the same peer for dupe purposes."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    q1 = _qso(a, t=t, remote_call="HB9NON")
    q2 = _qso(a, t=t + timedelta(minutes=10), remote_call="HB9NON/P")
    rs = [
        _record(q1, seeded_contest, status=ScoringStatus.HB9_QSO),
        _record(q2, seeded_contest, status=ScoringStatus.HB9_QSO),
    ]
    flipped = mark_dupes(rs)
    assert flipped == 1


@pytest.mark.django_db
def test_mark_dupes_preserves_matched_qso_pointer_on_loser(seeded_contest):
    """A deducted dupe still references its peer QSO so admins can see what got cut."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    q1 = _qso(a, t=t, remote_call="HB9ABC/P")
    q2 = _qso(a, t=t + timedelta(minutes=10), remote_call="HB9ABC/P")
    peer1 = _qso(b, t=t, remote_call="HB9TVK/P")
    peer2 = _qso(b, t=t + timedelta(minutes=10), remote_call="HB9TVK/P")
    rs = [
        _record(q1, seeded_contest, status=ScoringStatus.FULL_MATCH, matched_qso=peer1),
        _record(q2, seeded_contest, status=ScoringStatus.FULL_MATCH, matched_qso=peer2),
    ]
    mark_dupes(rs)
    loser = next(r for r in rs if r.status == ScoringStatus.DUPE_DEDUCTED)
    assert loser.matched_qso is not None  # pointer kept


@pytest.mark.django_db
def test_mark_dupes_different_participants_do_not_interfere(seeded_contest):
    """Bucket key is per participant. A and B can each have one QSO with HB9XYZ in the same half."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9XYZ/P")
    qb = _qso(b, t=t, remote_call="HB9XYZ/P")
    rs = [
        _record(qa, seeded_contest, status=ScoringStatus.FULL_MATCH),
        _record(qb, seeded_contest, status=ScoringStatus.FULL_MATCH),
    ]
    flipped = mark_dupes(rs)
    assert flipped == 0


# --- score_contest integration --------------------------------------------------------------
# These confirm the orchestrator wires dupe deduction into the scoring run.


@pytest.mark.django_db
def test_score_contest_marks_in_half_dupes(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    q_first = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    q_dupe = _qso(a, t=t + timedelta(minutes=15), remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    _qso(b, t=t + timedelta(minutes=15), remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)

    score_contest(seeded_contest)
    assert ScoringRecord.objects.get(qso=q_first).status == ScoringStatus.FULL_MATCH
    assert ScoringRecord.objects.get(qso=q_dupe).status == ScoringStatus.DUPE_DEDUCTED


@pytest.mark.django_db
def test_score_contest_keeps_zweitverbindung_across_halves(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    h1 = seeded_contest.start_utc
    h2 = seeded_contest.half_split_utc + timedelta(minutes=1)
    q_h1 = _qso(a, t=h1, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    q_h2 = _qso(a, t=h2, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    _qso(b, t=h1, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    _qso(b, t=h2, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)

    score_contest(seeded_contest)
    assert ScoringRecord.objects.get(qso=q_h1).status == ScoringStatus.FULL_MATCH
    assert ScoringRecord.objects.get(qso=q_h2).status == ScoringStatus.FULL_MATCH


@pytest.mark.django_db
def test_score_contest_deducts_non_nmd_dupes_across_halves(seeded_contest):
    """End-to-end: A works HB9NON twice (H1 + H2, same mode). Second is a
    non-NMD dupe — no Zweitverbindungen exception for non-NMD QSOs."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    h1 = seeded_contest.start_utc
    h2 = seeded_contest.half_split_utc + timedelta(minutes=1)
    q_first = _qso(a, t=h1, remote_call="HB9NON")
    q_dupe = _qso(a, t=h2, remote_call="HB9NON")

    score_contest(seeded_contest)
    assert ScoringRecord.objects.get(qso=q_first).status == ScoringStatus.HB9_QSO
    assert ScoringRecord.objects.get(qso=q_dupe).status == ScoringStatus.DUPE_DEDUCTED


@pytest.mark.django_db
def test_score_contest_summary_reflects_post_dupe_state(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    _qso(a, t=t + timedelta(minutes=10), remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    _qso(b, t=t + timedelta(minutes=10), remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)

    summary = score_contest(seeded_contest)
    # 4 QSOs total, paired into 2 buckets, one dupe deducted on each side.
    assert summary[ScoringStatus.FULL_MATCH] == 2
    assert summary[ScoringStatus.DUPE_DEDUCTED] == 2

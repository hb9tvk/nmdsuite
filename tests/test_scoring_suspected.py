"""M3.4 — suspected-wrong-callsign detector."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model

from core.models import Participant, QsoEntry, ScoringRecord, ScoringStatus
from scoring.pairing import MATCH_WINDOW, _qso_half, match_key, score_contest
from scoring.suspected import detect_suspected

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


def _scaffold(contest, participants):
    """Build the per-participant maps that score_contest constructs internally."""
    participants_by_key = {match_key(p.callsign): p for p in participants}
    key_by_participant_id = {p.id: k for k, p in participants_by_key.items()}
    qsos_by_key = {
        k: list(p.qsos.filter(utc_time__isnull=False).exclude(mode="").exclude(remote_call="").order_by("utc_time", "id"))
        for k, p in participants_by_key.items()
    }
    return participants_by_key, key_by_participant_id, qsos_by_key


def _record(qso, contest, *, status, suspected_correct_call=""):
    return ScoringRecord(
        qso=qso, status=status, matched_qso=None,
        text_distance=0, half=_qso_half(qso, contest),
        suspected_correct_call=suspected_correct_call,
    )


TXT_A = "HB9TVK PIZ KESCH 3418M"
TXT_B = "HB9ABC ALPSTEIN 2502M"
TXT_C = "HB9XYZ SAENTIS 2502M"


# --- detect_suspected (unit) -----------------------------------------------------------------


@pytest.mark.django_db
def test_suspected_upgrades_unmatched_when_another_participant_sent_matching_text(seeded_contest):
    """A says they worked HB9ABC. HB9ABC didn't log A. But C did transmit
    a text that's exactly what A received — so A probably misheard C as
    HB9ABC."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")  # registered but didn't log A
    c = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_C)
    _qso(c, t=t, remote_call="HB9DEF/P", txts=TXT_C, txtr="something unrelated 1234567")

    records = [_record(qa, seeded_contest, status=ScoringStatus.UNMATCHED)]
    pbk, kbpid, qbk = _scaffold(seeded_contest, [a, c])
    flipped = detect_suspected(records, qsos_by_key=qbk, participants_by_key=pbk, key_by_participant_id=kbpid)

    assert flipped == 1
    assert records[0].status == ScoringStatus.SUSPECTED_CALL_MISMATCH
    assert records[0].suspected_correct_call == c.callsign


@pytest.mark.django_db
def test_suspected_picks_closest_text_match_when_multiple_candidates(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    c1 = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ/P")
    c2 = _make_participant(seeded_contest, username="HB9DEF", callsign="HB9DEF/P")
    t = seeded_contest.start_utc
    # A received text TXT_C exactly. c1's transmission has 2 errors vs. TXT_C; c2's has 0.
    qa = _qso(a, t=t, remote_call="HB9NOBODY/P", txts=TXT_A, txtr=TXT_C)
    _qso(c1, t=t, remote_call="HB9DEF/P", txts="HB9XYZ SAENTIX 2503M", txtr="other text here 1234")  # 2 subs
    _qso(c2, t=t, remote_call="HB9TVK/P", txts=TXT_C, txtr="another text 1234567")  # exact

    records = [_record(qa, seeded_contest, status=ScoringStatus.UNMATCHED)]
    pbk, kbpid, qbk = _scaffold(seeded_contest, [a, c1, c2])
    detect_suspected(records, qsos_by_key=qbk, participants_by_key=pbk, key_by_participant_id=kbpid)

    # The exact-match candidate wins, not the 2-error one.
    assert records[0].suspected_correct_call == c2.callsign


@pytest.mark.django_db
def test_suspected_ignores_candidates_outside_time_window(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    c = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9NOBODY/P", txts=TXT_A, txtr=TXT_C)
    _qso(c, t=t + MATCH_WINDOW + timedelta(minutes=5), remote_call="HB9TVK/P",
         txts=TXT_C, txtr="x" * 20)

    records = [_record(qa, seeded_contest, status=ScoringStatus.UNMATCHED)]
    pbk, kbpid, qbk = _scaffold(seeded_contest, [a, c])
    detect_suspected(records, qsos_by_key=qbk, participants_by_key=pbk, key_by_participant_id=kbpid)
    assert records[0].status == ScoringStatus.UNMATCHED


@pytest.mark.django_db
def test_suspected_requires_same_mode(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    c = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, mode="CW", remote_call="HB9NOBODY/P", txts=TXT_A, txtr=TXT_C)
    _qso(c, t=t, mode="SSB", remote_call="HB9TVK/P", txts=TXT_C, txtr="x" * 20)

    records = [_record(qa, seeded_contest, status=ScoringStatus.UNMATCHED)]
    pbk, kbpid, qbk = _scaffold(seeded_contest, [a, c])
    detect_suspected(records, qsos_by_key=qbk, participants_by_key=pbk, key_by_participant_id=kbpid)
    assert records[0].status == ScoringStatus.UNMATCHED


@pytest.mark.django_db
def test_suspected_excludes_self_and_the_typed_callsign(seeded_contest):
    """Even if my own log or the participant I thought I worked has a matching
    text, neither is a valid suspected sender."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    # A logged the QSO; A's own log also has a coincidentally matching txts (shouldn't suggest self).
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_C, txtr=TXT_C)
    # B (the typed peer) ALSO transmitted matching text (shouldn't suggest the same call we typed).
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_C, txtr="other 1234567")

    records = [_record(qa, seeded_contest, status=ScoringStatus.UNMATCHED)]
    pbk, kbpid, qbk = _scaffold(seeded_contest, [a, b])
    detect_suspected(records, qsos_by_key=qbk, participants_by_key=pbk, key_by_participant_id=kbpid)
    assert records[0].status == ScoringStatus.UNMATCHED


@pytest.mark.django_db
def test_suspected_requires_non_empty_received_text(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    c = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9NOBODY/P", txts=TXT_A, txtr="")
    _qso(c, t=t, remote_call="HB9TVK/P", txts=TXT_C, txtr="x" * 20)

    records = [_record(qa, seeded_contest, status=ScoringStatus.UNMATCHED)]
    pbk, kbpid, qbk = _scaffold(seeded_contest, [a, c])
    flipped = detect_suspected(records, qsos_by_key=qbk, participants_by_key=pbk, key_by_participant_id=kbpid)
    assert flipped == 0


@pytest.mark.django_db
def test_suspected_skips_records_with_empty_candidate_txts(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    c = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9NOBODY/P", txts=TXT_A, txtr=TXT_C)
    _qso(c, t=t, remote_call="HB9TVK/P", txts="", txtr="x" * 20)  # candidate sent nothing

    records = [_record(qa, seeded_contest, status=ScoringStatus.UNMATCHED)]
    pbk, kbpid, qbk = _scaffold(seeded_contest, [a, c])
    flipped = detect_suspected(records, qsos_by_key=qbk, participants_by_key=pbk, key_by_participant_id=kbpid)
    assert flipped == 0


@pytest.mark.django_db
def test_suspected_does_not_touch_full_or_text_mismatch_or_hb9_or_dx(seeded_contest):
    """Only UNMATCHED is eligible for upgrade — other statuses stand."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    c = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ/P")
    t = seeded_contest.start_utc
    q_full = _qso(a, t=t, remote_call="HB9X", txts=TXT_A, txtr=TXT_C)
    q_tm = _qso(a, t=t + timedelta(minutes=5), remote_call="HB9X", txts=TXT_A, txtr=TXT_C)
    q_hb9 = _qso(a, t=t + timedelta(minutes=10), remote_call="HB9NON", txts=TXT_A, txtr=TXT_C)
    q_dx = _qso(a, t=t + timedelta(minutes=15), remote_call="DL1ABC", txts=TXT_A, txtr=TXT_C)
    _qso(c, t=t, remote_call="HB9TVK/P", txts=TXT_C, txtr="x" * 20)
    _qso(c, t=t + timedelta(minutes=5), remote_call="HB9TVK/P", txts=TXT_C, txtr="x" * 20)
    _qso(c, t=t + timedelta(minutes=10), remote_call="HB9TVK/P", txts=TXT_C, txtr="x" * 20)
    _qso(c, t=t + timedelta(minutes=15), remote_call="HB9TVK/P", txts=TXT_C, txtr="x" * 20)

    records = [
        _record(q_full, seeded_contest, status=ScoringStatus.FULL_MATCH),
        _record(q_tm, seeded_contest, status=ScoringStatus.TEXT_MISMATCH),
        _record(q_hb9, seeded_contest, status=ScoringStatus.HB9_QSO),
        _record(q_dx, seeded_contest, status=ScoringStatus.DX_QSO),
    ]
    pbk, kbpid, qbk = _scaffold(seeded_contest, [a, c])
    flipped = detect_suspected(records, qsos_by_key=qbk, participants_by_key=pbk, key_by_participant_id=kbpid)
    assert flipped == 0


# --- score_contest integration ---------------------------------------------------------------


@pytest.mark.django_db
def test_score_contest_flips_unmatched_to_suspected(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")  # registered but silent
    c = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_C)
    _qso(c, t=t, remote_call="HB9DEF/P", txts=TXT_C, txtr="x" * 20)

    score_contest(seeded_contest)
    r = ScoringRecord.objects.get(qso=qa)
    assert r.status == ScoringStatus.SUSPECTED_CALL_MISMATCH
    assert r.suspected_correct_call == c.callsign


@pytest.mark.django_db
def test_score_contest_suspected_outranks_unmatched_in_dedup(seeded_contest):
    """Same (peer, mode, half): one plain UNMATCHED + one SUSPECTED. The
    SUSPECTED row carries diagnostic info and must win over the plain one."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    c = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ/P")
    t = seeded_contest.start_utc

    # Two QSOs from A both claim to have worked HB9ABC. Only the second one
    # has txtr that matches C's txts, so only it will become SUSPECTED.
    q_plain = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr="some unrelated text 1234")
    q_suspect = _qso(a, t=t + timedelta(minutes=10), remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_C)
    _qso(c, t=t + timedelta(minutes=10), remote_call="HB9DEF/P", txts=TXT_C, txtr="x" * 20)

    score_contest(seeded_contest)
    r_plain = ScoringRecord.objects.get(qso=q_plain)
    r_suspect = ScoringRecord.objects.get(qso=q_suspect)
    # The SUSPECTED one is kept; the plain UNMATCHED is the dupe.
    assert r_suspect.status == ScoringStatus.SUSPECTED_CALL_MISMATCH
    assert r_plain.status == ScoringStatus.DUPE_DEDUCTED


@pytest.mark.django_db
def test_score_contest_full_match_still_outranks_suspected(seeded_contest):
    """A FULL_MATCH peer is much stronger evidence than a SUSPECTED guess —
    full wins in dedup."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    c = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ/P")
    t = seeded_contest.start_utc

    q_full = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    q_suspect = _qso(a, t=t + timedelta(minutes=10), remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_C)
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    _qso(c, t=t + timedelta(minutes=10), remote_call="HB9DEF/P", txts=TXT_C, txtr="x" * 20)

    score_contest(seeded_contest)
    assert ScoringRecord.objects.get(qso=q_full).status == ScoringStatus.FULL_MATCH
    # SUSPECTED was detected, but the FULL row wins; this one becomes DUPE.
    assert ScoringRecord.objects.get(qso=q_suspect).status == ScoringStatus.DUPE_DEDUCTED

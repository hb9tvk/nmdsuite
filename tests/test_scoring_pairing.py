"""M3.2 — pairing engine: classification + ScoringRecord persistence."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model

from core.models import Participant, QsoEntry, ScoringRecord, ScoringStatus
from scoring import pairing
from scoring.pairing import (
    MATCH_WINDOW,
    SWISS_PREFIXES,
    classify_qso,
    is_swiss_callsign,
    match_key,
    score_contest,
)

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


# 15-char exchange texts (rule §7); spaces don't count toward length.
TXT_A = "HB9TVK PIZ KESCH 3418M"
TXT_B = "HB9ABC ALPSTEIN 2502M"


# --- helpers (pure functions) ----------------------------------------------------------------


def test_match_key_strips_portable_suffix():
    assert match_key("HB9TVK/P") == "HB9TVK"
    assert match_key("HB9TVK") == "HB9TVK"
    assert match_key("HB9TVK/MM") == "HB9TVK"


def test_match_key_normalises_case_and_whitespace():
    assert match_key(" hb9tvk/p ") == "HB9TVK"


def test_is_swiss_callsign():
    for prefix in SWISS_PREFIXES:
        assert is_swiss_callsign(f"{prefix}XYZ") is True
    assert is_swiss_callsign("DL1ABC") is False
    assert is_swiss_callsign("oe5xxx") is False  # case-insensitive via normalize


# --- classify_qso (no DB orchestration) ------------------------------------------------------


@pytest.mark.django_db
def test_classify_non_participant_swiss_call_is_hb9(seeded_contest):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    q = _qso(p, t=seeded_contest.start_utc, remote_call="HB9XYZ", txts=TXT_A, txtr=TXT_B)
    result = classify_qso(q, peer_qsos=None)
    assert result.status == ScoringStatus.HB9_QSO
    assert result.matched_qso is None


@pytest.mark.django_db
def test_classify_non_participant_dx_call_is_dx(seeded_contest):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    q = _qso(p, t=seeded_contest.start_utc, remote_call="DL1ABC", txts=TXT_A, txtr=TXT_B)
    result = classify_qso(q, peer_qsos=None)
    assert result.status == ScoringStatus.DX_QSO


@pytest.mark.django_db
def test_classify_full_match_when_texts_align(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    qb = _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)

    result = classify_qso(qa, peer_qsos=[qb])
    assert result.status == ScoringStatus.FULL_MATCH
    assert result.matched_qso == qb
    assert result.text_distance == 0


@pytest.mark.django_db
def test_classify_full_match_tolerates_two_receiver_errors(seeded_contest):
    """Two errors in *my* txtr vs. *their* txts — still a full match for me.
    The sender's transmission is taken as ground truth."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    # Two subs in qa.txtr vs. qb.txts (X→C, 3→2). The other direction is clean.
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr="HB9ABX ALPSTEIN 2503M")
    qb = _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    result = classify_qso(qa, peer_qsos=[qb])
    assert result.status == ScoringStatus.FULL_MATCH
    assert result.text_distance == 2


@pytest.mark.django_db
def test_classify_is_asymmetric_per_receiver_direction(seeded_contest):
    """A receives B correctly; B mis-receives A by 3 chars. A gets FULL_MATCH,
    B gets TEXT_MISMATCH. Sender is always assumed correct, so the error is
    charged only to the side that mis-received."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)              # clean receive
    qb = _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr="HB9XYZ XYZ KESCH 3499M")  # 3+ errors

    ra = classify_qso(qa, peer_qsos=[qb])
    rb = classify_qso(qb, peer_qsos=[qa])
    assert ra.status == ScoringStatus.FULL_MATCH
    assert ra.text_distance == 0
    assert rb.status == ScoringStatus.TEXT_MISMATCH
    assert rb.text_distance > 2


@pytest.mark.django_db
def test_classify_fuzzy_match_when_peer_wrote_wrong_dxcall(seeded_contest):
    """Receiver got the dxcall right; sender wrote a wrong dxcall in their
    log. Texts match both ways within tolerance — receiver still gets
    FULL_MATCH via the fuzzy-pairing fallback."""
    a = _make_participant(seeded_contest, username="HB3XSS", callsign="HB3XSS/P")
    b = _make_participant(seeded_contest, username="HB3YRZ", callsign="HB3YRZ/P")
    t = seeded_contest.start_utc
    # A correctly logs B as the peer.
    qa = _qso(a, t=t, mode="SSB", rsts="55", rstr="55", remote_call="HB3YRZ/P",
              txts="au clair de la lune", txtr="turbina elettrica")
    # B wrote HB9XSS/P (wrong) instead of HB3XSS/P; their txts/txtr otherwise correct
    # (txtr has 1 extra 'e' — within tolerance).
    qb = _qso(b, t=t, mode="SSB", rsts="55", rstr="55", remote_call="HB9XSS/P",
              txts="turbina elettrica", txtr="au claire de la lune")

    # From A's side: peer_qsos = B's full log. my_key=HB3XSS.
    result = classify_qso(qa, peer_qsos=[qb], my_key="HB3XSS")
    assert result.status == ScoringStatus.FULL_MATCH
    assert result.matched_qso == qb
    assert result.text_distance == 0  # A received B perfectly


@pytest.mark.django_db
def test_classify_strict_match_takes_precedence_over_fuzzy(seeded_contest):
    """When both a strict candidate and a separate fuzzy candidate exist,
    strict wins — peer's recorded dxcall is strong evidence."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    # Strict candidate (correct dxcall) at the right time:
    strict = _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    # Bogus fuzzy candidate (wrong dxcall but matching texts) at almost the same time:
    fuzzy = _qso(b, t=t + timedelta(seconds=10), remote_call="HB9XXX/P", txts=TXT_B, txtr=TXT_A)

    result = classify_qso(qa, peer_qsos=[strict, fuzzy], my_key="HB9TVK")
    assert result.matched_qso == strict


@pytest.mark.django_db
def test_classify_fuzzy_requires_both_text_directions(seeded_contest):
    """If only one direction's text matches, that's too weak — stay UNMATCHED.
    Strict-callsign-match is what justifies single-direction (receiver) text
    scoring; without it we need both directions for confidence."""
    a = _make_participant(seeded_contest, username="HB3XSS", callsign="HB3XSS/P")
    b = _make_participant(seeded_contest, username="HB3YRZ", callsign="HB3YRZ/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, mode="SSB", rsts="55", rstr="55", remote_call="HB3YRZ/P",
              txts="au clair de la lune", txtr="turbina elettrica")
    # B's QSO has wrong dxcall AND only one direction matches — fuzzy must reject.
    qb = _qso(b, t=t, mode="SSB", rsts="55", rstr="55", remote_call="HB9XSS/P",
              txts="turbina elettrica", txtr="something completely different here")

    result = classify_qso(qa, peer_qsos=[qb], my_key="HB3XSS")
    assert result.status == ScoringStatus.UNMATCHED


@pytest.mark.django_db
def test_classify_strict_match_downgrades_to_suspected_when_dxcall_misses_portable_suffix(seeded_contest):
    """We typed HB3YMQ but the registered station is HB3YMQ/P. Pairing still
    finds them (lookup is portable-tolerant), but giving 4 points requires
    the dxcall to match the registered callsign exactly. Downgrade to
    SUSPECTED with the correct call surfaced as a hint."""
    a = _make_participant(seeded_contest, username="HB9CGA", callsign="HB9CGA/P")
    b = _make_participant(seeded_contest, username="HB3YMQ", callsign="HB3YMQ/P")
    t = seeded_contest.start_utc
    txt_a = "Fernausbreitung 0123456"
    txt_b = "Blumenstraeusse 0123456"
    qa = _qso(a, t=t, remote_call="HB3YMQ", txts=txt_a, txtr=txt_b)  # missing /P
    qb = _qso(b, t=t, remote_call="HB9CGA/P", txts=txt_b, txtr=txt_a)  # correct

    result = classify_qso(qa, peer_qsos=[qb], my_key="HB9CGA", peer_callsign="HB3YMQ/P")
    assert result.status == ScoringStatus.SUSPECTED_CALL_MISMATCH
    assert result.matched_qso == qb
    assert result.suspected_correct_call == "HB3YMQ/P"


@pytest.mark.django_db
def test_classify_full_match_when_dxcall_matches_registered_callsign_exactly(seeded_contest):
    """Sanity check: when peer_callsign is provided AND we typed it right,
    we still get FULL_MATCH (i.e. providing peer_callsign doesn't break
    the happy path)."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    qb = _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    result = classify_qso(qa, peer_qsos=[qb], my_key="HB9TVK", peer_callsign="HB9ABC/P")
    assert result.status == ScoringStatus.FULL_MATCH


@pytest.mark.django_db
def test_classify_fuzzy_match_also_downgrades_on_call_mismatch(seeded_contest):
    """Even if we'd be saved by a fuzzy peer-side-typo rescue, typing the
    peer's call wrong on our side still costs us the points."""
    a = _make_participant(seeded_contest, username="HB3XSS", callsign="HB3XSS/P")
    b = _make_participant(seeded_contest, username="HB3YRZ", callsign="HB3YRZ/P")
    t = seeded_contest.start_utc
    # We typed HB3YRZ (missing /P) — wrong on our side.
    qa = _qso(a, t=t, mode="SSB", rsts="55", rstr="55", remote_call="HB3YRZ",
              txts="au clair de la lune", txtr="turbina elettrica")
    # Peer also typed our call wrong (HB9XSS instead of HB3XSS) — wrong on their side.
    qb = _qso(b, t=t, mode="SSB", rsts="55", rstr="55", remote_call="HB9XSS/P",
              txts="turbina elettrica", txtr="au claire de la lune")
    result = classify_qso(qa, peer_qsos=[qb], my_key="HB3XSS", peer_callsign="HB3YRZ/P")
    # Fuzzy would have given us FULL_MATCH for the peer-typo case, but our own
    # call typo dominates → SUSPECTED.
    assert result.status == ScoringStatus.SUSPECTED_CALL_MISMATCH
    assert result.suspected_correct_call == "HB3YRZ/P"


@pytest.mark.django_db
def test_classify_text_mismatch_when_distance_exceeds_tolerance(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr="WRONG TEXT NOTHING ALIGNED")
    qb = _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    result = classify_qso(qa, peer_qsos=[qb])
    assert result.status == ScoringStatus.TEXT_MISMATCH
    assert result.matched_qso == qb
    assert result.text_distance > 2


@pytest.mark.django_db
def test_classify_unmatched_when_peer_log_has_nothing_back_at_us(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    q = _qso(a, t=seeded_contest.start_utc, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    # peer is a participant but logged nothing back at us
    result = classify_qso(q, peer_qsos=[])
    assert result.status == ScoringStatus.UNMATCHED
    assert result.matched_qso is None


@pytest.mark.django_db
def test_classify_empty_text_is_text_mismatch_not_full(seeded_contest):
    # Two participants both logged a QSO but neither typed an exchange text.
    # Distance is 0 but this isn't a real NMD QSO — must NOT be FULL_MATCH.
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts="", txtr="")
    qb = _qso(b, t=t, remote_call="HB9TVK/P", txts="", txtr="")
    result = classify_qso(qa, peer_qsos=[qb])
    assert result.status == ScoringStatus.TEXT_MISMATCH
    assert result.matched_qso == qb


@pytest.mark.django_db
def test_classify_outside_time_window_is_unmatched(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    # Peer logged the QSO 10 minutes later — outside the ±5 min window.
    qb = _qso(b, t=t + MATCH_WINDOW + timedelta(minutes=5), remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    result = classify_qso(qa, peer_qsos=[qb])
    assert result.status == ScoringStatus.UNMATCHED


@pytest.mark.django_db
def test_classify_picks_closest_in_time_when_multiple_candidates(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc + timedelta(minutes=30)
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    far = _qso(b, t=t - timedelta(minutes=3), remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    near = _qso(b, t=t + timedelta(seconds=10), remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    result = classify_qso(qa, peer_qsos=[far, near])
    assert result.matched_qso == near


def test_match_window_is_ten_minutes():
    """Sanity guard: real-world validation needed ±10 min to catch
    legitimate pairs whose timestamps drift up to ~10 min apart. Keep
    this test as a tripwire if the constant ever changes."""
    assert MATCH_WINDOW == timedelta(minutes=10)


@pytest.mark.django_db
def test_classify_only_matches_same_mode(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, mode="CW", remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    qb = _qso(b, t=t, mode="SSB", remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    result = classify_qso(qa, peer_qsos=[qb])
    assert result.status == ScoringStatus.UNMATCHED


# --- score_contest (orchestration + ScoringRecord persistence) -------------------------------


@pytest.mark.django_db
def test_score_contest_creates_records_for_both_sides_of_a_pair(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    qb = _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)

    summary = score_contest(seeded_contest)
    assert summary == {ScoringStatus.FULL_MATCH: 2}

    ra = ScoringRecord.objects.get(qso=qa)
    rb = ScoringRecord.objects.get(qso=qb)
    assert ra.status == ScoringStatus.FULL_MATCH
    assert ra.matched_qso == qb
    assert rb.matched_qso == qa
    assert ra.text_distance == 0


@pytest.mark.django_db
def test_score_contest_records_half_from_utc_time(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    q1 = _qso(a, t=seeded_contest.start_utc, remote_call="DL1ABC")  # H1
    q2 = _qso(a, t=seeded_contest.half_split_utc + timedelta(minutes=1), remote_call="DL1ABC")  # H2

    score_contest(seeded_contest)
    assert ScoringRecord.objects.get(qso=q1).half == 1
    assert ScoringRecord.objects.get(qso=q2).half == 2


@pytest.mark.django_db
def test_score_contest_skips_rows_with_null_utc_or_blank_mode_or_blank_remote(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    bad_utc = QsoEntry.objects.create(
        participant=a, utc_raw="bad", utc_time=None, mode="CW",
        remote_call="HB9ABC", rsts="599", rstr="599",
    )
    bad_mode = QsoEntry.objects.create(
        participant=a, utc_raw="0700", utc_time=seeded_contest.start_utc, mode="",
        remote_call="HB9ABC", rsts="599", rstr="599",
    )
    bad_remote = QsoEntry.objects.create(
        participant=a, utc_raw="0700", utc_time=seeded_contest.start_utc, mode="CW",
        remote_call="", rsts="599", rstr="599",
    )

    score_contest(seeded_contest)
    assert not ScoringRecord.objects.filter(qso__in=[bad_utc, bad_mode, bad_remote]).exists()


@pytest.mark.django_db
def test_score_contest_excludes_cancelled_participants(seeded_contest):
    from django.utils import timezone

    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    b.cancelled_at = timezone.now()
    b.save(update_fields=["cancelled_at"])

    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    # B's QSO never gets scored. A's QSO should look unmatched because B
    # isn't a participant anymore (treated as HB9 instead).
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)

    score_contest(seeded_contest)
    assert ScoringRecord.objects.get(qso=qa).status == ScoringStatus.HB9_QSO
    assert not ScoringRecord.objects.filter(qso__participant=b).exists()


@pytest.mark.django_db
def test_score_contest_is_idempotent(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)

    summary1 = score_contest(seeded_contest)
    summary2 = score_contest(seeded_contest)
    assert summary1 == summary2
    assert ScoringRecord.objects.count() == 2


@pytest.mark.django_db
def test_score_contest_rescues_qso_when_peer_wrote_wrong_dxcall(seeded_contest):
    """End-to-end: receiver got dxcall right, sender wrote a wrong dxcall in
    their log. Receiver should get FULL_MATCH (via fuzzy pairing); sender
    should be SUSPECTED_CALL_MISMATCH (via detect_suspected promoting their
    HB9_QSO once it sees the text matches a registered station)."""
    a = _make_participant(seeded_contest, username="HB3XSS", callsign="HB3XSS/P")
    b = _make_participant(seeded_contest, username="HB3YRZ", callsign="HB3YRZ/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, mode="SSB", rsts="55", rstr="55", remote_call="HB3YRZ/P",
              txts="au clair de la lune", txtr="turbina elettrica")
    qb = _qso(b, t=t, mode="SSB", rsts="55", rstr="55", remote_call="HB9XSS/P",
              txts="turbina elettrica", txtr="au claire de la lune")

    score_contest(seeded_contest)
    ra = ScoringRecord.objects.get(qso=qa)
    rb = ScoringRecord.objects.get(qso=qb)
    assert ra.status == ScoringStatus.FULL_MATCH
    assert ra.matched_qso == qb
    assert ra.points == 4
    assert rb.status == ScoringStatus.SUSPECTED_CALL_MISMATCH
    assert rb.suspected_correct_call == a.callsign
    assert rb.points == 0


@pytest.mark.django_db
def test_score_contest_paired_qso_across_h1_h2_boundary_uses_canonical_half(seeded_contest):
    """Boundary edge case: with ±10 min window, A at 07:55 (H1) pairs with
    B at 08:05 (H2). Both ScoringRecords must end up with the *same* half
    (the earlier of the two timestamps → H1) so that dupe deduction
    treats the physical QSO symmetrically.

    Without canonical-half, B would see two H2 QSOs (this paired one +
    the 09:30 dupe) and lose one to dedup, while A would see H1+H2 and
    keep both — unfair asymmetry caused purely by clock drift."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    split = seeded_contest.half_split_utc
    a_early = split - timedelta(minutes=5)  # 07:55, H1 by own time
    b_early = split + timedelta(minutes=5)  # 08:05, H2 by own time
    h2_late = split + timedelta(minutes=90)  # 09:30, clearly H2 on both sides

    qa1 = _qso(a, t=a_early, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    qa2 = _qso(a, t=h2_late, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    qb1 = _qso(b, t=b_early, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    qb2 = _qso(b, t=h2_late, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)

    score_contest(seeded_contest)
    ra1 = ScoringRecord.objects.get(qso=qa1)
    rb1 = ScoringRecord.objects.get(qso=qb1)
    # Canonical half = min(qa1.utc_time, qb1.utc_time) → a_early → H1.
    assert ra1.half == 1
    assert rb1.half == 1
    # No dupes — each side has H1 + H2.
    assert ra1.status == ScoringStatus.FULL_MATCH
    assert rb1.status == ScoringStatus.FULL_MATCH
    assert ScoringRecord.objects.get(qso=qa2).status == ScoringStatus.FULL_MATCH
    assert ScoringRecord.objects.get(qso=qb2).status == ScoringStatus.FULL_MATCH


@pytest.mark.django_db
def test_score_contest_boundary_qso_logged_after_close_scores_both_sides(seeded_contest):
    """A QSO that started at 09:59 but whose *end* one side logged as 10:02.

    The permissive parser leaves the out-of-window row with ``utc_time =
    None`` (only the raw HHMM is kept). The QSO still started before 10:00,
    so both sides must score — the operator who logged correctly must not be
    denied points because their peer logged the end time. The reconstructed
    timestamp used to pair the straggler is transient: the row stays null in
    the DB (and out-of-window in the log editor)."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t_in = seeded_contest.end_utc.replace(second=0)  # 09:59:00, in window

    qa = _qso(a, t=t_in, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    # B logged the QSO's end at 10:02: out of window, so utc_time is null but
    # the raw HHMM is well-formed — exactly what portal.qso_service stores.
    qb = QsoEntry.objects.create(
        participant=b, utc_raw="1002", utc_time=None, mode="CW",
        remote_call="HB9TVK/P", rsts="599", rstr="599", txts=TXT_B, txtr=TXT_A,
    )

    score_contest(seeded_contest)

    ra = ScoringRecord.objects.get(qso=qa)
    rb = ScoringRecord.objects.get(qso=qb)
    assert ra.status == ScoringStatus.FULL_MATCH
    assert rb.status == ScoringStatus.FULL_MATCH
    assert ra.points == 4
    assert rb.points == 4
    assert ra.matched_qso == qb
    assert rb.matched_qso == qa
    # The reconstructed timestamp is in-memory only — never persisted.
    qb.refresh_from_db()
    assert qb.utc_time is None


@pytest.mark.django_db
def test_score_contest_out_of_window_qso_without_mate_is_dropped(seeded_contest):
    """A straggler with no in-window counterpart to anchor it does not score
    — unchanged from before, when a null ``utc_time`` dropped it outright."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    qa = QsoEntry.objects.create(
        participant=a, utc_raw="1002", utc_time=None, mode="CW",
        remote_call="DL1ABC", rsts="599", rstr="599",
    )
    score_contest(seeded_contest)
    assert not ScoringRecord.objects.filter(qso=qa).exists()


@pytest.mark.django_db
def test_score_contest_both_sides_after_close_do_not_score(seeded_contest):
    """When both operators place the QSO after 10:00, the earlier of the two
    timestamps is still out of window — the contact started too late, so
    neither side scores."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    qa = QsoEntry.objects.create(
        participant=a, utc_raw="1002", utc_time=None, mode="CW",
        remote_call="HB9ABC/P", rsts="599", rstr="599", txts=TXT_A, txtr=TXT_B,
    )
    qb = QsoEntry.objects.create(
        participant=b, utc_raw="1003", utc_time=None, mode="CW",
        remote_call="HB9TVK/P", rsts="599", rstr="599", txts=TXT_B, txtr=TXT_A,
    )
    score_contest(seeded_contest)
    assert not ScoringRecord.objects.filter(qso__in=[qa, qb]).exists()


@pytest.mark.django_db
def test_score_contest_dxcall_missing_portable_suffix_loses_points(seeded_contest):
    """End-to-end reproduction of the user's bug report: HB9CGA/P logged
    HB3YMQ (no /P) instead of HB3YMQ/P. The receiver loses their 4 points;
    the other side (which got the call right) keeps theirs."""
    a = _make_participant(seeded_contest, username="HB9CGA", callsign="HB9CGA/P")
    b = _make_participant(seeded_contest, username="HB3YMQ", callsign="HB3YMQ/P")
    t = seeded_contest.start_utc
    txt_a = "Fernausbreitung extra-padding-xx"
    txt_b = "Blumenstraeusse extra-padding-x"
    qa = _qso(a, t=t, remote_call="HB3YMQ", txts=txt_a, txtr=txt_b)        # missing /P
    qb = _qso(b, t=t, remote_call="HB9CGA/P", txts=txt_b, txtr=txt_a)      # correct

    score_contest(seeded_contest)
    ra = ScoringRecord.objects.get(qso=qa)
    rb = ScoringRecord.objects.get(qso=qb)
    assert ra.status == ScoringStatus.SUSPECTED_CALL_MISMATCH
    assert ra.suspected_correct_call == "HB3YMQ/P"
    assert ra.points == 0
    assert rb.status == ScoringStatus.FULL_MATCH
    assert rb.points == 4


@pytest.mark.django_db
def test_score_contest_handles_portable_suffix_asymmetry(seeded_contest):
    """When the dxcall is typed without /P but the peer is registered with /P,
    the lookup still finds them (so we don't misclassify as HB9_QSO); the
    /P mismatch then downgrades the receiver to SUSPECTED. The other side
    (which got the call right) keeps FULL_MATCH."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC", txts=TXT_A, txtr=TXT_B)  # no /P
    qb = _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)  # correct

    score_contest(seeded_contest)
    assert ScoringRecord.objects.get(qso=qa).status == ScoringStatus.SUSPECTED_CALL_MISMATCH
    assert ScoringRecord.objects.get(qso=qa).suspected_correct_call == "HB9ABC/P"
    assert ScoringRecord.objects.get(qso=qb).status == ScoringStatus.FULL_MATCH


@pytest.mark.django_db
def test_score_contest_classifies_mixed_log(seeded_contest):
    """One log with NMD-match + NMD-unmatched + Swiss-non-participant + DX, scored together.

    "Unmatched" means the remote *is* a registered participant but didn't log
    the QSO back at us. A Swiss-but-not-registered remote is HB9_QSO, never
    UNMATCHED — the registration list is the source of truth.
    """
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    # C is registered but logs nothing — A's QSO with C will be UNMATCHED.
    _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ/P")
    t = seeded_contest.start_utc
    # Spread the QSOs out so they don't fall within MATCH_WINDOW of B's
    # transmission at `t` — otherwise suspected detection would promote
    # the UNMATCHED row to SUSPECTED via B's txts matching.
    nmd_match = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    nmd_unmatched = _qso(a, t=t + timedelta(minutes=30), remote_call="HB9XYZ/P", txts=TXT_A, txtr=TXT_B)
    swiss = _qso(a, t=t + timedelta(minutes=60), remote_call="HB9NON")
    dx = _qso(a, t=t + timedelta(minutes=90), remote_call="DL1ABC")
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)

    score_contest(seeded_contest)
    assert ScoringRecord.objects.get(qso=nmd_match).status == ScoringStatus.FULL_MATCH
    assert ScoringRecord.objects.get(qso=nmd_unmatched).status == ScoringStatus.UNMATCHED
    assert ScoringRecord.objects.get(qso=swiss).status == ScoringStatus.HB9_QSO
    assert ScoringRecord.objects.get(qso=dx).status == ScoringStatus.DX_QSO


@pytest.mark.django_db
def test_score_contest_summary_counts(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    _qso(a, t=t + timedelta(minutes=10), remote_call="DL1ABC")
    _qso(a, t=t + timedelta(minutes=20), remote_call="HB9XYZ")  # Swiss non-participant
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)

    summary = score_contest(seeded_contest)
    assert summary[ScoringStatus.FULL_MATCH] == 2  # both sides of the pair
    assert summary[ScoringStatus.DX_QSO] == 1
    assert summary[ScoringStatus.HB9_QSO] == 1

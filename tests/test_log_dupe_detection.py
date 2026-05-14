"""Soft duplicate detection in the participant log entry view.

Saves stay permissive — the participant can keep the dupe in their log
(maybe only one of the two is the real QSO; they need to decide). We just
flag rows so the operator can spot the typo before final submission.

Mirrors the scoring-side dupe rules:
- Same (peer, mode, half): always flagged.
- Same (peer, mode) across halves: flagged ONLY if the peer is not a
  registered NMD station (NMD↔NMD is allowed once per half = up to two
  total across the contest).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model

from core.models import Participant, QsoEntry
from portal.qso_service import detect_potential_dupe_ids, list_qsos_with_dupe_flags

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


def _qso(p, *, t, remote_call, mode="CW", rsts="599", rstr="599"):
    return QsoEntry.objects.create(
        participant=p, utc_raw=t.strftime("%H%M"), utc_time=t, mode=mode,
        remote_call=remote_call, rsts=rsts, rstr=rstr,
    )


# --- detect_potential_dupe_ids (unit) --------------------------------------------------------


@pytest.mark.django_db
def test_same_half_nmd_repeat_is_flagged(seeded_contest):
    """Two QSOs with the same NMD peer in the same mode and half — even an
    NMD peer can't be worked twice in one half (Zweitverbindungen are H1+H2)."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    q1 = _qso(a, t=t, remote_call="HB9ABC/P")
    q2 = _qso(a, t=t + timedelta(minutes=20), remote_call="HB9ABC/P")

    dupe_ids = detect_potential_dupe_ids(a)
    assert {q1.id, q2.id} <= dupe_ids


@pytest.mark.django_db
def test_cross_half_nmd_repeat_is_not_flagged(seeded_contest):
    """Zweitverbindung: two NMD QSOs with the same (peer, mode) across halves
    are allowed — do NOT flag."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    h1 = seeded_contest.start_utc
    h2 = seeded_contest.half_split_utc + timedelta(minutes=1)
    q1 = _qso(a, t=h1, remote_call="HB9ABC/P")
    q2 = _qso(a, t=h2, remote_call="HB9ABC/P")

    dupe_ids = detect_potential_dupe_ids(a)
    assert q1.id not in dupe_ids
    assert q2.id not in dupe_ids


@pytest.mark.django_db
def test_cross_half_non_nmd_repeat_is_flagged(seeded_contest):
    """Non-NMD: once per (peer, mode) for the whole contest. Cross-half
    repetition is still a dupe."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    # HB9NON is NOT registered as an NMD participant.
    h1 = seeded_contest.start_utc
    h2 = seeded_contest.half_split_utc + timedelta(minutes=1)
    q1 = _qso(a, t=h1, remote_call="HB9NON")
    q2 = _qso(a, t=h2, remote_call="HB9NON")

    dupe_ids = detect_potential_dupe_ids(a)
    assert {q1.id, q2.id} <= dupe_ids


@pytest.mark.django_db
def test_different_modes_are_not_dupes(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    q_cw = _qso(a, t=t, mode="CW", rsts="599", rstr="599", remote_call="HB9ABC/P")
    q_ssb = _qso(a, t=t + timedelta(minutes=5), mode="SSB", rsts="59", rstr="59", remote_call="HB9ABC/P")

    dupe_ids = detect_potential_dupe_ids(a)
    assert q_cw.id not in dupe_ids
    assert q_ssb.id not in dupe_ids


@pytest.mark.django_db
def test_portable_suffix_does_not_split_buckets(seeded_contest):
    """``HB9ABC`` and ``HB9ABC/P`` collapse to the same peer for dupe detection."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    q1 = _qso(a, t=t, remote_call="HB9ABC")
    q2 = _qso(a, t=t + timedelta(minutes=10), remote_call="HB9ABC/P")

    dupe_ids = detect_potential_dupe_ids(a)
    assert {q1.id, q2.id} <= dupe_ids


@pytest.mark.django_db
def test_rows_with_missing_data_are_ignored(seeded_contest):
    """Permissive saves with null utc_time / blank mode / blank remote_call
    can't be evaluated — just skip them."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    valid = _qso(a, t=t, remote_call="HB9ABC")
    QsoEntry.objects.create(participant=a, utc_raw="bad", utc_time=None, mode="",
                            remote_call="", rsts="", rstr="")
    QsoEntry.objects.create(participant=a, utc_raw="0700", utc_time=t + timedelta(hours=1),
                            mode="", remote_call="HB9ABC", rsts="599", rstr="599")

    dupe_ids = detect_potential_dupe_ids(a)
    assert valid.id not in dupe_ids  # only one usable, no pair


@pytest.mark.django_db
def test_three_in_same_half_all_flagged(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    q1 = _qso(a, t=t, remote_call="HB9NON")
    q2 = _qso(a, t=t + timedelta(minutes=5), remote_call="HB9NON")
    q3 = _qso(a, t=t + timedelta(minutes=10), remote_call="HB9NON")

    dupe_ids = detect_potential_dupe_ids(a)
    assert dupe_ids == {q1.id, q2.id, q3.id}


# --- list_qsos_with_dupe_flags (annotation wrapper) ------------------------------------------


@pytest.mark.django_db
def test_list_qsos_annotates_is_potential_dupe(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    dupe = _qso(a, t=t, remote_call="HB9NON")
    _qso(a, t=t + timedelta(minutes=10), remote_call="HB9NON")
    unique = _qso(a, t=t + timedelta(minutes=30), remote_call="DL1ABC")

    flagged = {q.id: q.is_potential_dupe for q in list_qsos_with_dupe_flags(a)}
    assert flagged[dupe.id] is True
    assert flagged[unique.id] is False


# --- view integration ------------------------------------------------------------------------


@pytest.mark.django_db
def test_log_entry_page_renders_dupe_row_class(client, seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    _qso(a, t=t, remote_call="HB9NON")
    _qso(a, t=t + timedelta(minutes=10), remote_call="HB9NON")
    user = a.user
    client.force_login(user)

    response = client.get("/submission/log/")
    assert response.status_code == 200
    assert b"qso-row-dupe" in response.content

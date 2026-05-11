"""M3.5 — ScoringOverride reattachment across re-scoring runs."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model

from core.models import Participant, QsoEntry, ScoringOverride, ScoringRecord, ScoringStatus
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


def _qso(p, *, t, remote_call, mode="CW", rsts="599", rstr="599", txts="", txtr=""):
    return QsoEntry.objects.create(
        participant=p, utc_raw=t.strftime("%H%M"), utc_time=t, mode=mode,
        remote_call=remote_call, rsts=rsts, rstr=rstr, txts=txts, txtr=txtr,
    )


TXT_A = "HB9TVK PIZ KESCH 3418M"
TXT_B = "HB9ABC ALPSTEIN 2502M"


@pytest.mark.django_db
def test_override_forces_admin_accepted_on_a_text_mismatch(seeded_contest):
    """A logged HB9ABC with bad text; B logged back with their txts. Default
    classification is TEXT_MISMATCH (0 points). Admin marks it ADMIN_ACCEPTED."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr="totally wrong text 99999")
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    ScoringOverride.objects.create(
        participant=a, utc_time=t, remote_call="HB9ABC/P", mode="CW",
        forced_status=ScoringStatus.ADMIN_ACCEPTED, comment="confirmed by SOTA logs",
    )

    score_contest(seeded_contest)
    r = ScoringRecord.objects.get(qso=qa)
    assert r.status == ScoringStatus.ADMIN_ACCEPTED
    assert r.points == 4
    assert r.admin_overridden is True
    assert r.admin_comment == "confirmed by SOTA logs"


@pytest.mark.django_db
def test_override_survives_a_second_score_run(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    ScoringOverride.objects.create(
        participant=a, utc_time=t, remote_call="HB9ABC/P", mode="CW",
        forced_status=ScoringStatus.ADMIN_ACCEPTED, comment="",
    )

    score_contest(seeded_contest)
    score_contest(seeded_contest)  # re-run
    r = ScoringRecord.objects.get(qso=qa)
    assert r.status == ScoringStatus.ADMIN_ACCEPTED
    assert r.admin_overridden is True


@pytest.mark.django_db
def test_removing_an_override_reverts_admin_overridden_flag(seeded_contest):
    """If admin deletes the override, the next score run should not pretend it's still applied."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    o = ScoringOverride.objects.create(
        participant=a, utc_time=t, remote_call="HB9ABC/P", mode="CW",
        forced_status=ScoringStatus.ADMIN_ACCEPTED, comment="",
    )

    score_contest(seeded_contest)
    assert ScoringRecord.objects.get(qso=qa).admin_overridden is True

    o.delete()
    score_contest(seeded_contest)
    r = ScoringRecord.objects.get(qso=qa)
    # No override means the natural classification (UNMATCHED — peer silent) returns.
    assert r.status == ScoringStatus.UNMATCHED
    assert r.admin_overridden is False
    assert r.admin_comment == ""


@pytest.mark.django_db
def test_override_key_is_normalised_for_portable_suffix(seeded_contest):
    """Override written against 'HB9ABC' applies after participant re-uploads with /P."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    ScoringOverride.objects.create(
        participant=a, utc_time=t, remote_call="HB9ABC", mode="CW",  # no /P
        forced_status=ScoringStatus.ADMIN_ACCEPTED, comment="",
    )

    score_contest(seeded_contest)
    assert ScoringRecord.objects.get(qso=qa).status == ScoringStatus.ADMIN_ACCEPTED


@pytest.mark.django_db
def test_admin_accepted_wins_dedup_over_full_match(seeded_contest):
    """Two QSOs in the same (peer, mode, half) bucket: one classifies as FULL_MATCH
    naturally, the other is admin-forced ADMIN_ACCEPTED. Admin's decision must win."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    # Naturally full-match QSO (B logged back at A).
    q_full = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    # Second QSO claiming the same peer; bad text → would be TEXT_MISMATCH. Admin overrides.
    q_admin = _qso(a, t=t + timedelta(minutes=10), remote_call="HB9ABC/P", txts=TXT_A, txtr="garbled 99999999")
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    _qso(b, t=t + timedelta(minutes=10), remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    ScoringOverride.objects.create(
        participant=a, utc_time=t + timedelta(minutes=10), remote_call="HB9ABC/P", mode="CW",
        forced_status=ScoringStatus.ADMIN_ACCEPTED, comment="",
    )

    score_contest(seeded_contest)
    r_full = ScoringRecord.objects.get(qso=q_full)
    r_admin = ScoringRecord.objects.get(qso=q_admin)
    assert r_admin.status == ScoringStatus.ADMIN_ACCEPTED  # kept
    assert r_full.status == ScoringStatus.DUPE_DEDUCTED    # deduped because admin outranks


@pytest.mark.django_db
def test_override_force_dupe_deducted_invalidates_a_qso(seeded_contest):
    """Admin can also force DUPE_DEDUCTED to manually remove a QSO from the count."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    qa = _qso(a, t=t, remote_call="HB9ABC/P", txts=TXT_A, txtr=TXT_B)
    _qso(b, t=t, remote_call="HB9TVK/P", txts=TXT_B, txtr=TXT_A)
    ScoringOverride.objects.create(
        participant=a, utc_time=t, remote_call="HB9ABC/P", mode="CW",
        forced_status=ScoringStatus.DUPE_DEDUCTED, comment="referee disqualified",
    )

    score_contest(seeded_contest)
    r = ScoringRecord.objects.get(qso=qa)
    assert r.status == ScoringStatus.DUPE_DEDUCTED
    assert r.points == 0
    assert r.admin_comment == "referee disqualified"


@pytest.mark.django_db
def test_override_with_no_matching_qso_does_not_crash(seeded_contest):
    """Stale override (operator deleted the QSO) is silently ignored."""
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    ScoringOverride.objects.create(
        participant=a, utc_time=t, remote_call="HB9ABC/P", mode="CW",
        forced_status=ScoringStatus.ADMIN_ACCEPTED, comment="",
    )
    # No corresponding QSO in A's log — should still score cleanly.
    score_contest(seeded_contest)
    assert ScoringRecord.objects.count() == 0

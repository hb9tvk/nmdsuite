"""M3.5 — run_scoring management command."""
from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from core.models import Contest, Participant, QsoEntry, ScoringRecord, ScoringStatus

User = get_user_model()


TXT_A = "HB9TVK PIZ KESCH 3418M"
TXT_B = "HB9ABC ALPSTEIN 2502M"


def _make_participant(contest, *, username, callsign):
    user = User.objects.create_user(username=username, password="x", email=f"{username.lower()}@x.org")
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign, first_name="X",
        email=f"{username.lower()}@x.org", coord_system_input="wgs84",
        coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2, ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", operating_modes=3,
    )


@pytest.mark.django_db
def test_run_scoring_uses_active_contest_when_no_year(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9ABC", callsign="HB9ABC/P")
    t = seeded_contest.start_utc
    QsoEntry.objects.create(
        participant=a, utc_raw="0612", utc_time=t, mode="CW",
        remote_call="HB9ABC/P", rsts="599", rstr="599", txts=TXT_A, txtr=TXT_B,
    )
    QsoEntry.objects.create(
        participant=b, utc_raw="0612", utc_time=t, mode="CW",
        remote_call="HB9TVK/P", rsts="599", rstr="599", txts=TXT_B, txtr=TXT_A,
    )

    out = StringIO()
    call_command("run_scoring", stdout=out)
    output = out.getvalue()
    assert "Scoring NMD 2026" in output
    assert "2 QSOs scored" in output
    assert ScoringRecord.objects.filter(status=ScoringStatus.FULL_MATCH).count() == 2


@pytest.mark.django_db
def test_run_scoring_year_flag_picks_specific_contest(seeded_contest):
    out = StringIO()
    call_command("run_scoring", "--year", str(seeded_contest.year), stdout=out)
    assert f"NMD {seeded_contest.year}" in out.getvalue()


@pytest.mark.django_db
def test_run_scoring_unknown_year_errors(seeded_contest):
    with pytest.raises(CommandError, match="No contest with year=9999"):
        call_command("run_scoring", "--year", "9999")


@pytest.mark.django_db
def test_run_scoring_no_active_contest_errors(db):
    # No contest seeded — no active contest exists.
    with pytest.raises(CommandError, match="No active contest"):
        call_command("run_scoring")


@pytest.mark.django_db
def test_run_scoring_archived_contest_not_picked_by_default(db):
    """Archived contests must be excluded from the default auto-pick."""
    from datetime import date, datetime, time, timezone
    Contest.objects.create(
        year=2020, contest_date=date(2020, 7, 19),
        start_utc=datetime(2020, 7, 19, 6, tzinfo=timezone.utc),
        half_split_utc=datetime(2020, 7, 19, 8, tzinfo=timezone.utc),
        end_utc=datetime(2020, 7, 19, 9, 59, 59, tzinfo=timezone.utc),
        state=Contest.State.ARCHIVED,
    )
    with pytest.raises(CommandError, match="No active contest"):
        call_command("run_scoring")


@pytest.mark.django_db
def test_run_scoring_verbose_prints_per_participant_breakdown(seeded_contest):
    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    t = seeded_contest.start_utc
    QsoEntry.objects.create(
        participant=a, utc_raw="0612", utc_time=t, mode="CW",
        remote_call="HB9NON", rsts="599", rstr="599",
    )
    out = StringIO()
    call_command("run_scoring", "-v", "2", stdout=out)
    output = out.getvalue()
    assert "Per-participant points" in output
    assert "HB9TVK/P" in output
    assert "total 1" in output  # one HB9_QSO worth 1 pt

"""export_legacy — write a contest into the legacy TCL-scorer schema."""
from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import Participant, QsoEntry
from scoring.management.commands.export_legacy import _EPOCH_2000

User = get_user_model()


# --- fixtures --------------------------------------------------------------------------------


def _make_participant(contest, *, username, callsign, weight=0):
    user = User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org",
    )
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign, first_name="X",
        email=f"{username.lower()}@x.org", coord_system_input="wgs84",
        coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2, ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", operating_modes=3, total_weight_g=weight,
    )


def _qso(p, *, t, remote_call, mode="CW", rsts="599", rstr="599", txts="", txtr=""):
    return QsoEntry.objects.create(
        participant=p, utc_raw=t.strftime("%H%M"), utc_time=t, mode=mode,
        remote_call=remote_call, rsts=rsts, rstr=rstr, txts=txts, txtr=txtr,
    )


def _connect(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# --- schema ----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_creates_legacy_schema(seeded_contest, tmp_path):
    out = tmp_path / "legacy.db"
    call_command("export_legacy", "--year", "2026", str(out))
    conn = _connect(out)
    try:
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    assert {"config", "nmdlog", "nmdstn"} <= tables


# --- nmdstn ----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_stations_get_portable_suffix_and_weight(seeded_contest, tmp_path):
    _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK", weight=4059)
    out = tmp_path / "legacy.db"
    call_command("export_legacy", "--year", "2026", str(out))
    conn = _connect(out)
    try:
        row = conn.execute("SELECT nmdstn, weight FROM nmdstn").fetchone()
    finally:
        conn.close()
    assert row["nmdstn"] == "HB9TVK/P"
    assert row["weight"] == 4059


@pytest.mark.django_db
def test_prefixed_callsign_kept_verbatim(seeded_contest, tmp_path):
    """A callsign that already carries a suffix/prefix ('/') is not given
    another /P — mirrors import_legacy's convention."""
    _make_participant(seeded_contest, username="HB9TVK", callsign="OE/HB9TVK")
    out = tmp_path / "legacy.db"
    call_command("export_legacy", "--year", "2026", str(out))
    conn = _connect(out)
    try:
        row = conn.execute("SELECT nmdstn FROM nmdstn").fetchone()
    finally:
        conn.close()
    assert row["nmdstn"] == "OE/HB9TVK"


@pytest.mark.django_db
def test_station_included_even_with_no_qsos(seeded_contest, tmp_path):
    """A registered participant who logged nothing must still appear in
    nmdstn, so the legacy scorer treats them as an NMD station."""
    _make_participant(seeded_contest, username="HB9AAA", callsign="HB9AAA")
    out = tmp_path / "legacy.db"
    call_command("export_legacy", "--year", "2026", str(out))
    conn = _connect(out)
    try:
        stns = [r[0] for r in conn.execute("SELECT nmdstn FROM nmdstn")]
        n_log = conn.execute("SELECT COUNT(*) FROM nmdlog").fetchone()[0]
    finally:
        conn.close()
    assert stns == ["HB9AAA/P"]
    assert n_log == 0


@pytest.mark.django_db
def test_cancelled_participant_excluded(seeded_contest, tmp_path):
    from django.utils import timezone

    p = _make_participant(seeded_contest, username="HB9GONE", callsign="HB9GONE")
    p.cancelled_at = timezone.now()
    p.save(update_fields=["cancelled_at"])
    out = tmp_path / "legacy.db"
    call_command("export_legacy", "--year", "2026", str(out))
    conn = _connect(out)
    try:
        assert conn.execute("SELECT COUNT(*) FROM nmdstn").fetchone()[0] == 0
    finally:
        conn.close()


# --- nmdlog ----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_qso_fields_and_utc_mapping(seeded_contest, tmp_path):
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK")
    t = seeded_contest.start_utc.replace(hour=8, minute=21)
    _qso(
        p, t=t, remote_call="HB9HZU", mode="CW",
        rsts="599", rstr="588", txts="SENT TEXT", txtr="RCVD TEXT",
    )
    out = tmp_path / "legacy.db"
    call_command("export_legacy", "--year", "2026", str(out))
    conn = _connect(out)
    try:
        row = conn.execute("SELECT * FROM nmdlog").fetchone()
    finally:
        conn.close()

    assert row["mode"] == "CW"
    assert row["localcall"] == "HB9TVK/P"
    assert row["dxcall"] == "HB9HZU"
    # RST/text columns keep the legacy grouping: rsts, rstr, txts, txtr.
    assert row["rsts"] == "599"
    assert row["rstr"] == "588"
    assert row["txts"] == "SENT TEXT"
    assert row["txtr"] == "RCVD TEXT"
    # Referee-decision columns are neutral so the legacy scorer re-derives them.
    assert (row["match"], row["status"], row["comment"]) == (0, 0, "")
    # 08:21 UTC anchored onto 2000-01-01.
    assert row["utc"] == _EPOCH_2000 + 8 * 3600 + 21 * 60


@pytest.mark.django_db
def test_unscorable_qsos_are_skipped(seeded_contest, tmp_path):
    """Rows the engine ignores — null utc_time, blank mode, blank
    remote_call — must not reach nmdlog either."""
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK")
    good = _qso(p, t=seeded_contest.start_utc, remote_call="HB9HZU")
    QsoEntry.objects.create(  # null utc_time
        participant=p, utc_raw="bad", utc_time=None, mode="CW", remote_call="HB9AAA",
    )
    QsoEntry.objects.create(  # blank mode
        participant=p, utc_raw="0700", utc_time=seeded_contest.start_utc,
        mode="", remote_call="HB9BBB",
    )
    QsoEntry.objects.create(  # blank remote_call
        participant=p, utc_raw="0700", utc_time=seeded_contest.start_utc,
        mode="CW", remote_call="",
    )
    out = tmp_path / "legacy.db"
    call_command("export_legacy", "--year", "2026", str(out))
    conn = _connect(out)
    try:
        rows = conn.execute("SELECT dxcall FROM nmdlog").fetchall()
    finally:
        conn.close()
    assert [r["dxcall"] for r in rows] == ["HB9HZU"]
    assert good.remote_call == "HB9HZU"


@pytest.mark.django_db
def test_ids_are_unique_across_participants(seeded_contest, tmp_path):
    a = _make_participant(seeded_contest, username="HB9AAA", callsign="HB9AAA")
    b = _make_participant(seeded_contest, username="HB9BBB", callsign="HB9BBB")
    base = seeded_contest.start_utc
    _qso(a, t=base, remote_call="HB9BBB")
    _qso(a, t=base + timedelta(minutes=1), remote_call="HB9CCC")
    _qso(b, t=base, remote_call="HB9AAA")
    out = tmp_path / "legacy.db"
    call_command("export_legacy", "--year", "2026", str(out))
    conn = _connect(out)
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM nmdlog")]
    finally:
        conn.close()
    assert len(ids) == len(set(ids)) == 3


# --- guardrails ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_refuses_to_overwrite_without_force(seeded_contest, tmp_path):
    out = tmp_path / "legacy.db"
    out.write_bytes(b"existing")
    with pytest.raises(CommandError):
        call_command("export_legacy", "--year", "2026", str(out))


@pytest.mark.django_db
def test_force_overwrites(seeded_contest, tmp_path):
    out = tmp_path / "legacy.db"
    out.write_bytes(b"existing")
    call_command("export_legacy", "--year", "2026", "--force", str(out))
    conn = _connect(out)
    try:
        # A real SQLite DB now — the schema query succeeds.
        conn.execute("SELECT COUNT(*) FROM nmdstn").fetchone()
    finally:
        conn.close()


@pytest.mark.django_db
def test_unknown_year_errors(seeded_contest, tmp_path):
    with pytest.raises(CommandError):
        call_command("export_legacy", "--year", "1999", str(tmp_path / "x.db"))


# --- round-trip against the real importer ----------------------------------------------------


@pytest.mark.django_db
def test_round_trip_through_import_legacy(seeded_contest, tmp_path):
    """export_legacy → import_legacy preserves the QSO fields that matter,
    proving the utc convention agrees with the importer's own reading."""
    p = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK")
    peer = _make_participant(seeded_contest, username="HB9HZU", callsign="HB9HZU")
    t = seeded_contest.start_utc.replace(hour=7, minute=42)
    _qso(p, t=t, remote_call="HB9HZU/P", mode="CW",
         rsts="599", rstr="599", txts="ALPHA", txtr="BRAVO")
    _qso(peer, t=t, remote_call="HB9TVK/P", mode="CW",
         rsts="599", rstr="599", txts="BRAVO", txtr="ALPHA")

    out = tmp_path / "legacy.db"
    call_command("export_legacy", "--year", "2026", str(out))

    # Import into a fresh contest and read HB9TVK's QSO back.
    call_command("seed_contest", "--year", "2027")
    call_command("import_legacy", "--year", "2027", str(out))

    from core.models import Contest

    reimported = Participant.objects.get(contest=Contest.objects.get(year=2027), callsign="HB9TVK/P")
    q = reimported.qsos.get()
    assert q.utc_raw == "0742"
    assert q.mode == "CW"
    assert q.remote_call == "HB9HZU/P"
    assert q.txts == "ALPHA"
    assert q.txtr == "BRAVO"

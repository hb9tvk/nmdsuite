"""import_legacy management command — legacy TCL SQLite dump loader."""
from __future__ import annotations

import sqlite3
from datetime import datetime, time, timezone
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from core.models import Participant, QsoEntry

User = get_user_model()


# --- helpers ---------------------------------------------------------------------------------


def _make_legacy_db(path, rows: list[tuple], *, registered: list[str] | None = None) -> None:
    """Create a legacy-shaped sqlite DB at ``path`` and load it.

    ``rows`` go into ``nmdlog`` (tuple order matches the production schema:
    id, mode, localcall, dxcall, utc, rsts, rstr, txts, txtr, match, status, comment).

    ``registered`` is the list of NMD-registered callsigns to insert into
    ``nmdstn``. Default = derived from distinct ``rows[2]`` (localcall), so
    most tests can ignore it and behave as if every logger was registered.
    Pass an explicit list (incl. ``[]``) to exercise the unregistered/
    fallback paths.
    """
    if registered is None:
        registered = sorted({r[2] for r in rows if r[2]})

    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE config (nmddate integer);
        CREATE TABLE nmdlog (id integer, mode text, localcall text, dxcall text,
                             utc integer, rsts text, rstr text, txts text, txtr text,
                             match integer, status integer, comment text);
        CREATE TABLE nmdstn (nmdstn text, weight integer);
        """
    )
    conn.executemany(
        "INSERT INTO nmdlog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.executemany(
        "INSERT INTO nmdstn VALUES (?, ?)",
        [(c, 0) for c in registered],
    )
    conn.commit()
    conn.close()


def _epoch(contest, hh: int, mm: int) -> int:
    """Unix epoch seconds for ``HH:MM`` UTC on the contest date."""
    return int(datetime.combine(contest.contest_date, time(hh, mm, tzinfo=timezone.utc)).timestamp())


TXT_TVK = "HB9TVK PIZ KESCH 3418M"
TXT_ABC = "HB9ABC ALPSTEIN 2502M"


def _sample_rows(contest):
    """A tiny legacy DB: HB9TVK/P + HB9ABC/P pair at 06:12, HB9TVK also worked DL1XYZ at 07:00."""
    t1 = _epoch(contest, 6, 12)
    t2 = _epoch(contest, 7, 0)
    return [
        # From HB9TVK/P's log
        (1, "cw",  "HB9TVK/P", "HB9ABC/P", t1, "599", "599", TXT_TVK, TXT_ABC, 0, 0, ""),
        (2, "ssb", "HB9TVK/P", "DL1XYZ",   t2, "59",  "59",  "",       "",       0, 0, ""),
        # From HB9ABC/P's log
        (3, "cw",  "HB9ABC/P", "HB9TVK/P", t1, "599", "599", TXT_ABC, TXT_TVK, 0, 0, ""),
    ]


# --- tests -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_import_legacy_creates_users_and_participants(seeded_contest, tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db, _sample_rows(seeded_contest))
    out = StringIO()
    call_command("import_legacy", "--year", str(seeded_contest.year), str(db), stdout=out)

    # Two unique localcalls → two Users (bare), two Participants (with /P preserved).
    assert User.objects.filter(username__in=["HB9TVK", "HB9ABC"]).count() == 2
    a = Participant.objects.get(callsign="HB9TVK/P")
    b = Participant.objects.get(callsign="HB9ABC/P")
    assert QsoEntry.objects.filter(participant=a).count() == 2
    assert QsoEntry.objects.filter(participant=b).count() == 1

    output = out.getvalue()
    assert "Imported 2 registered participant(s)" in output
    assert "3 QSOs" in output


@pytest.mark.django_db
def test_import_legacy_converts_utc_epoch_to_hhmm(seeded_contest, tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db, _sample_rows(seeded_contest))
    call_command("import_legacy", "--year", str(seeded_contest.year), str(db), stdout=StringIO())

    a = Participant.objects.get(callsign="HB9TVK/P")
    qsos = list(QsoEntry.objects.filter(participant=a).order_by("utc_raw"))
    assert [q.utc_raw for q in qsos] == ["0612", "0700"]
    # utc_time should be parseable to the right time-of-day on the contest date.
    assert qsos[0].utc_time.hour == 6 and qsos[0].utc_time.minute == 12
    assert qsos[1].utc_time.hour == 7 and qsos[1].utc_time.minute == 0


@pytest.mark.django_db
def test_import_legacy_preserves_portable_suffix_from_db(seeded_contest, tmp_path):
    """If the localcall already has /P in the DB, don't double-append."""
    db = tmp_path / "legacy.db"
    _make_legacy_db(db, _sample_rows(seeded_contest))
    call_command("import_legacy", "--year", str(seeded_contest.year), str(db), stdout=StringIO())
    a = Participant.objects.get(user__username="HB9TVK")
    assert a.callsign == "HB9TVK/P"
    # No double-/P:
    assert not a.callsign.endswith("/P/P")


@pytest.mark.django_db
def test_import_legacy_appends_portable_suffix_when_missing(seeded_contest, tmp_path):
    db = tmp_path / "legacy.db"
    t1 = _epoch(seeded_contest, 6, 12)
    _make_legacy_db(db, [
        (1, "cw", "HB9XYZ", "DL1ABC", t1, "599", "599", "x" * 20, "y" * 20, 0, 0, ""),
    ])
    call_command("import_legacy", "--year", str(seeded_contest.year), str(db), stdout=StringIO())
    # Bare localcall → /P appended by default.
    assert Participant.objects.get(user__username="HB9XYZ").callsign == "HB9XYZ/P"


@pytest.mark.django_db
def test_import_legacy_no_portable_suffix_flag_disables_auto_append(seeded_contest, tmp_path):
    db = tmp_path / "legacy.db"
    t1 = _epoch(seeded_contest, 6, 12)
    _make_legacy_db(db, [
        (1, "cw", "HB9XYZ", "DL1ABC", t1, "599", "599", "x" * 20, "y" * 20, 0, 0, ""),
    ])
    call_command(
        "import_legacy", "--year", str(seeded_contest.year), str(db),
        "--no-portable-suffix", stdout=StringIO(),
    )
    assert Participant.objects.get(user__username="HB9XYZ").callsign == "HB9XYZ"


@pytest.mark.django_db
def test_import_legacy_is_idempotent(seeded_contest, tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db, _sample_rows(seeded_contest))
    call_command("import_legacy", "--year", str(seeded_contest.year), str(db), stdout=StringIO())
    call_command("import_legacy", "--year", str(seeded_contest.year), str(db), stdout=StringIO())

    assert User.objects.filter(username="HB9TVK").count() == 1
    a = Participant.objects.get(callsign="HB9TVK/P")
    assert QsoEntry.objects.filter(participant=a).count() == 2


@pytest.mark.django_db
def test_import_legacy_skips_null_utc_rows(seeded_contest, tmp_path):
    """Legacy data sometimes has NULL/bad utc; those rows are dropped, not crashed on."""
    db = tmp_path / "legacy.db"
    t1 = _epoch(seeded_contest, 6, 12)
    _make_legacy_db(db, [
        (1, "cw", "HB9TVK/P", "HB9ABC/P", t1,  "599", "599", "x" * 20, "y" * 20, 0, 0, ""),
        (2, "cw", "HB9TVK/P", "HB9XYZ/P", None,"599", "599", "x" * 20, "y" * 20, 0, 0, ""),
    ])
    call_command("import_legacy", "--year", str(seeded_contest.year), str(db), stdout=StringIO())
    a = Participant.objects.get(callsign="HB9TVK/P")
    assert QsoEntry.objects.filter(participant=a).count() == 1


@pytest.mark.django_db
def test_import_legacy_unknown_year_errors(seeded_contest, tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db, [])
    with pytest.raises(CommandError, match="No contest with year=9999"):
        call_command("import_legacy", "--year", "9999", str(db))


@pytest.mark.django_db
def test_import_legacy_missing_file_errors(seeded_contest):
    with pytest.raises(CommandError, match="Not a file"):
        call_command("import_legacy", "--year", str(seeded_contest.year), "/no/such/file.db")


@pytest.mark.django_db
def test_import_legacy_empty_db_errors(seeded_contest, tmp_path):
    db = tmp_path / "empty.db"
    _make_legacy_db(db, [], registered=[])
    with pytest.raises(CommandError, match="neither nmdstn rows nor nmdlog rows"):
        call_command("import_legacy", "--year", str(seeded_contest.year), str(db))


@pytest.mark.django_db
def test_import_legacy_creates_participant_for_registered_station_with_no_log(seeded_contest, tmp_path):
    """A station can register in nmdstn without submitting a log.
    They must still become a Participant so peer QSOs with them
    classify as UNMATCHED, not HB9_QSO."""
    db = tmp_path / "legacy.db"
    _make_legacy_db(
        db,
        [
            # Only HB9TVK logged; HB9ABC is registered but submitted nothing.
            (1, "cw", "HB9TVK/P", "HB9ABC/P", _epoch(seeded_contest, 6, 12),
             "599", "599", TXT_TVK, TXT_ABC, 0, 0, ""),
        ],
        registered=["HB9TVK/P", "HB9ABC/P"],
    )
    call_command("import_legacy", "--year", str(seeded_contest.year), str(db), stdout=StringIO())

    a = Participant.objects.get(callsign="HB9TVK/P")
    b = Participant.objects.get(callsign="HB9ABC/P")
    assert QsoEntry.objects.filter(participant=a).count() == 1
    assert QsoEntry.objects.filter(participant=b).count() == 0


@pytest.mark.django_db
def test_import_legacy_unmatched_when_registered_peer_is_silent(seeded_contest, tmp_path):
    """End-to-end semantics fix: HB9ABC registered but logged nothing.
    HB9TVK's QSO with them must be UNMATCHED, NOT HB9_QSO."""
    from core.models import ScoringRecord, ScoringStatus
    from scoring.pairing import score_contest

    db = tmp_path / "legacy.db"
    _make_legacy_db(
        db,
        [
            (1, "cw", "HB9TVK/P", "HB9ABC/P", _epoch(seeded_contest, 6, 12),
             "599", "599", TXT_TVK, TXT_ABC, 0, 0, ""),
        ],
        registered=["HB9TVK/P", "HB9ABC/P"],
    )
    call_command("import_legacy", "--year", str(seeded_contest.year), str(db), stdout=StringIO())
    score_contest(seeded_contest)

    tvk_qso = QsoEntry.objects.get(participant__callsign="HB9TVK/P", remote_call="HB9ABC/P")
    assert ScoringRecord.objects.get(qso=tvk_qso).status == ScoringStatus.UNMATCHED


@pytest.mark.django_db
def test_import_legacy_drops_unregistered_loggers(seeded_contest, tmp_path):
    """An nmdlog entry whose localcall isn't in nmdstn is skipped with a warning."""
    db = tmp_path / "legacy.db"
    t1 = _epoch(seeded_contest, 6, 12)
    _make_legacy_db(
        db,
        [
            (1, "cw", "HB9TVK/P", "HB9ABC/P", t1, "599", "599", TXT_TVK, TXT_ABC, 0, 0, ""),
            (2, "cw", "HB9FAKE",  "HB9TVK/P", t1, "599", "599", "x" * 20, "y" * 20, 0, 0, ""),
        ],
        registered=["HB9TVK/P", "HB9ABC/P"],  # HB9FAKE NOT registered
    )
    out = StringIO()
    call_command("import_legacy", "--year", str(seeded_contest.year), str(db), stdout=out)
    output = out.getvalue()

    assert not Participant.objects.filter(callsign__contains="FAKE").exists()
    assert "unregistered logger: HB9FAKE" in output
    assert "dropped 1 unregistered logger" in output


@pytest.mark.django_db
def test_import_legacy_falls_back_to_distinct_localcalls_when_nmdstn_empty(seeded_contest, tmp_path):
    """Old legacy dumps might have an empty nmdstn — fall back so the engine
    can still be exercised, but warn that the NMD/non-NMD distinction is fuzzy."""
    db = tmp_path / "legacy.db"
    t1 = _epoch(seeded_contest, 6, 12)
    _make_legacy_db(
        db,
        [
            (1, "cw", "HB9TVK/P", "HB9ABC/P", t1, "599", "599", TXT_TVK, TXT_ABC, 0, 0, ""),
        ],
        registered=[],
    )
    out = StringIO()
    call_command("import_legacy", "--year", str(seeded_contest.year), str(db), stdout=out)
    output = out.getvalue()
    assert "nmdstn is empty" in output
    assert Participant.objects.get(callsign="HB9TVK/P")


@pytest.mark.django_db
def test_import_legacy_pipes_into_run_scoring(seeded_contest, tmp_path):
    """End-to-end: import + score in one go, like the validation workflow."""
    db = tmp_path / "legacy.db"
    _make_legacy_db(db, _sample_rows(seeded_contest))
    call_command("import_legacy", "--year", str(seeded_contest.year), str(db), stdout=StringIO())

    out = StringIO()
    call_command("run_scoring", "--year", str(seeded_contest.year), stdout=out)
    output = out.getvalue()
    assert "3 QSOs scored" in output
    assert "full_match" in output  # the HB9TVK ↔ HB9ABC pair
    assert "dx_qso" in output      # HB9TVK ↔ DL1XYZ

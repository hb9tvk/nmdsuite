"""import_submission management command — Flask submission.sqlite3 loader."""
from __future__ import annotations

import sqlite3
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from core.models import (
    Participant,
    QsoEntry,
    StationComponent,
)

User = get_user_model()


# --- helpers ---------------------------------------------------------------------------------


def _make_submission_db(
    path,
    *,
    users=None,
    stations=None,
    logs=None,
) -> None:
    """Create a legacy submission.sqlite3 at ``path`` and populate it.

    Schema mirrors ``reference/nmdlogsubmission/app.py``. Tables are
    created empty by default; pass dict/tuple lists to fill them.
    """
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE user (
            id INTEGER PRIMARY KEY, callsign TEXT, password TEXT, email TEXT
        );
        CREATE TABLE station_description (
            id INTEGER PRIMARY KEY, callsign TEXT, opname TEXT, email TEXT,
            ort TEXT, kanton TEXT, koord_x TEXT, koord_y TEXT,
            qah INTEGER, watt TEXT,
            sta01bez TEXT, sta01gramm INTEGER,
            sta02bez TEXT, sta02gramm INTEGER,
            sta03bez TEXT, sta03gramm INTEGER,
            sta04bez TEXT, sta04gramm INTEGER,
            sta05bez TEXT, sta05gramm INTEGER,
            sta06bez TEXT, sta06gramm INTEGER,
            sta07bez TEXT, sta07gramm INTEGER,
            sta08bez TEXT, sta08gramm INTEGER,
            sta09bez TEXT, sta09gramm INTEGER,
            sta10bez TEXT, sta10gramm INTEGER,
            sta11bez TEXT, sta11gramm INTEGER,
            gesamtegewicht INTEGER,
            submitted INTEGER
        );
        CREATE TABLE log_entry (
            id INTEGER PRIMARY KEY, callsign TEXT, utc TEXT, remote_call TEXT,
            rsts TEXT, txts TEXT, rstr TEXT, txtr TEXT
        );
    """)
    for u in users or []:
        conn.execute(
            "INSERT INTO user (callsign, password, email) VALUES (?, ?, ?)",
            (u["callsign"], u.get("password", "x"), u.get("email")),
        )
    for s in stations or []:
        cols = ["callsign", "opname", "email", "ort", "kanton",
                "koord_x", "koord_y", "qah", "watt"]
        vals: list = [s.get(c) for c in cols]
        for i in range(1, 12):
            cols.append(f"sta{i:02d}bez")
            cols.append(f"sta{i:02d}gramm")
            vals.append(s.get(f"sta{i:02d}bez"))
            vals.append(s.get(f"sta{i:02d}gramm"))
        cols.extend(["gesamtegewicht", "submitted"])
        vals.extend([s.get("gesamtegewicht", 0), 1 if s.get("submitted") else 0])
        placeholders = ",".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO station_description ({','.join(cols)}) VALUES ({placeholders})",
            vals,
        )
    for r in logs or []:
        conn.execute(
            "INSERT INTO log_entry (callsign, utc, remote_call, rsts, txts, rstr, txtr) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (r["callsign"], r["utc"], r["remote_call"],
             r["rsts"], r.get("txts", ""), r["rstr"], r.get("txtr", "")),
        )
    conn.commit()
    conn.close()


def _run(tmp_path, **kwargs) -> str:
    """Invoke the management command, return captured stdout."""
    db = tmp_path / "submission.sqlite3"
    _make_submission_db(db, **kwargs)
    out = StringIO()
    call_command("import_submission", str(db), "--year", "2026", stdout=out)
    return out.getvalue()


# --- account + participant -------------------------------------------------------------------


@pytest.mark.django_db
def test_creates_user_and_participant_for_each_callsign(seeded_contest, tmp_path):
    _run(
        tmp_path,
        users=[{"callsign": "HB9TVK", "email": "tvk@x.org"}],
        stations=[{
            "callsign": "HB9TVK", "opname": "Peter", "kanton": "ZH", "qah": 1395,
            "submitted": True,
        }],
        logs=[],
    )
    assert User.objects.filter(username="HB9TVK").count() == 1
    assert Participant.objects.filter(contest=seeded_contest, callsign="HB9TVK").count() == 1


@pytest.mark.django_db
def test_portable_suffix_stripped_on_import(seeded_contest, tmp_path):
    """Legacy entries that carry /P are normalised to the bare callsign;
    both User.username and Participant.callsign end up identical."""
    _run(
        tmp_path,
        users=[{"callsign": "HB9TVK/P", "email": "t@x.org"}],
        stations=[{"callsign": "HB9TVK/P", "submitted": True}],
    )
    assert User.objects.filter(username="HB9TVK").exists()
    assert not User.objects.filter(username="HB9TVK/P").exists()
    p = Participant.objects.get(callsign="HB9TVK")
    assert p.user.username == "HB9TVK"


@pytest.mark.django_db
def test_bare_callsign_stays_bare(seeded_contest, tmp_path):
    """A legacy entry without /P also stays bare — we never auto-append."""
    _run(
        tmp_path,
        users=[{"callsign": "HB9X"}],
        stations=[{"callsign": "HB9X", "submitted": True}],
    )
    p = Participant.objects.get(user__username="HB9X")
    assert p.callsign == "HB9X"


@pytest.mark.django_db
def test_submitted_flag_drives_submitted_at(seeded_contest, tmp_path):
    _run(
        tmp_path,
        users=[{"callsign": "HB9A"}, {"callsign": "HB9B"}],
        stations=[
            {"callsign": "HB9A", "submitted": True},
            {"callsign": "HB9B", "submitted": False},
        ],
    )
    a = Participant.objects.get(callsign="HB9A")
    b = Participant.objects.get(callsign="HB9B")
    assert a.submitted_at is not None
    assert b.submitted_at is None


@pytest.mark.django_db
def test_opname_lands_in_first_name(seeded_contest, tmp_path):
    _run(
        tmp_path,
        users=[{"callsign": "HB9TVK"}],
        stations=[{"callsign": "HB9TVK", "opname": "Peter", "submitted": True}],
    )
    assert Participant.objects.get(callsign="HB9TVK").first_name == "Peter"


# --- station description + components --------------------------------------------------------


@pytest.mark.django_db
def test_station_description_persists_full_payload(seeded_contest, tmp_path):
    _run(
        tmp_path,
        users=[{"callsign": "HB9A"}],
        stations=[{
            "callsign": "HB9A", "opname": "Anna", "ort": "Albispass",
            "kanton": "ZH", "qah": 900, "watt": "5W",
            "sta01bez": "FT-857", "sta01gramm": 1500,
            "sta02bez": "LiFePO4 12V", "sta02gramm": 1200,
            "sta05bez": "Linked dipole", "sta05gramm": 500,
            "gesamtegewicht": 3200, "submitted": True,
        }],
    )
    p = Participant.objects.get(callsign="HB9A")
    # The legacy station_description's fields are merged onto Participant
    # (migration 0007).
    assert p.location_text == "Albispass"
    assert p.op_name == "Anna"
    assert p.watt == "5W"
    assert p.total_weight_g == 3200

    comps = {c.idx: (c.description, c.weight_g) for c in p.components.all()}
    assert comps[1] == ("FT-857", 1500)
    assert comps[2] == ("LiFePO4 12V", 1200)
    assert comps[5] == ("Linked dipole", 500)
    # Empty slots aren't materialised.
    assert 3 not in comps
    assert 11 not in comps


@pytest.mark.django_db
def test_no_station_row_means_empty_equipment_fields(seeded_contest, tmp_path):
    """A logger that exists in user but not in station_description still
    becomes a Participant — with the equipment fields left at their
    defaults."""
    _run(
        tmp_path,
        users=[{"callsign": "HB9A"}],
        stations=[],
    )
    p = Participant.objects.get(callsign="HB9A")
    assert p.op_name == ""
    assert p.watt == ""
    assert p.total_weight_g == 0
    assert not p.components.exists()


# --- coordinates -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_lv03_coordinates_parse(seeded_contest, tmp_path):
    """Legacy logger stored CH1903 LV03 (6-digit). Should round-trip
    to CH1903+ and WGS84."""
    _run(
        tmp_path,
        users=[{"callsign": "HB9A"}],
        stations=[{
            "callsign": "HB9A", "koord_x": "681241", "koord_y": "237069",
            "submitted": True,
        }],
    )
    p = Participant.objects.get(callsign="HB9A")
    # LV95 should be ~2.68M east, 1.24M north — i.e. LV03 + LV95 offsets.
    assert 2_680_000 < p.ch1903p_e < 2_700_000
    assert 1_230_000 < p.ch1903p_n < 1_250_000
    assert 46.0 < p.wgs84_lat < 48.0
    assert 5.0 < p.wgs84_lon < 11.0


@pytest.mark.django_db
def test_invalid_coordinates_fall_back_to_stub(seeded_contest, tmp_path):
    """Outside-Switzerland coordinates → import still succeeds, raw
    inputs preserved, canonical fields stay at the stub value so the
    participant is still importable."""
    _run(
        tmp_path,
        users=[{"callsign": "HB9X"}],
        stations=[{
            "callsign": "HB9X", "koord_x": "1", "koord_y": "1",
            "submitted": True,
        }],
    )
    p = Participant.objects.get(callsign="HB9X")
    # Raw text preserved; canonical fields fell back to stubs.
    assert p.coord_input_e == "1"
    assert p.coord_input_n == "1"
    assert p.ch1903p_e == 2_600_000.0


# --- QSO log import --------------------------------------------------------------------------


@pytest.mark.django_db
def test_log_rows_become_qso_entries(seeded_contest, tmp_path):
    _run(
        tmp_path,
        users=[{"callsign": "HB9A"}],
        stations=[{"callsign": "HB9A", "submitted": True}],
        logs=[
            {"callsign": "HB9A", "utc": "0700", "remote_call": "HB9B",
             "rsts": "599", "txts": "test1", "rstr": "599", "txtr": "test1"},
            {"callsign": "HB9A", "utc": "0815", "remote_call": "HB9C",
             "rsts": "59", "txts": "test2", "rstr": "59", "txtr": "test2"},
        ],
    )
    p = Participant.objects.get(callsign="HB9A")
    rows = list(QsoEntry.objects.filter(participant=p).order_by("utc_raw"))
    assert len(rows) == 2
    assert {r.mode for r in rows} == {"CW", "SSB"}  # inferred from RST length


@pytest.mark.django_db
def test_operating_modes_inferred_from_log(seeded_contest, tmp_path):
    """RST length 2 → SSB, 3 → CW. operating_modes is the bitmask of
    what the participant actually used."""
    _run(
        tmp_path,
        users=[
            {"callsign": "HB9CW"},
            {"callsign": "HB9SSB"},
            {"callsign": "HB9BOTH"},
            {"callsign": "HB9NONE"},
        ],
        stations=[
            {"callsign": "HB9CW", "submitted": True},
            {"callsign": "HB9SSB", "submitted": True},
            {"callsign": "HB9BOTH", "submitted": True},
            {"callsign": "HB9NONE", "submitted": True},
        ],
        logs=[
            {"callsign": "HB9CW", "utc": "0700", "remote_call": "HB9X",
             "rsts": "599", "rstr": "599"},
            {"callsign": "HB9SSB", "utc": "0700", "remote_call": "HB9X",
             "rsts": "59", "rstr": "59"},
            {"callsign": "HB9BOTH", "utc": "0700", "remote_call": "HB9X",
             "rsts": "599", "rstr": "599"},
            {"callsign": "HB9BOTH", "utc": "0815", "remote_call": "HB9Y",
             "rsts": "59", "rstr": "59"},
        ],
    )
    cw = Participant.objects.get(callsign="HB9CW")
    ssb = Participant.objects.get(callsign="HB9SSB")
    both = Participant.objects.get(callsign="HB9BOTH")
    none = Participant.objects.get(callsign="HB9NONE")
    assert cw.operating_modes == Participant.Mode.CW
    assert ssb.operating_modes == Participant.Mode.SSB
    assert both.operating_modes == Participant.Mode.BOTH
    # No QSOs → default BOTH (we don't know what they intended).
    assert none.operating_modes == Participant.Mode.BOTH


# --- idempotency + edge cases ----------------------------------------------------------------


@pytest.mark.django_db
def test_rerunning_import_is_idempotent(seeded_contest, tmp_path):
    """Re-importing the same DB updates the existing Participant in place
    and replaces the QSO list — no duplicate rows."""
    db = tmp_path / "submission.sqlite3"
    _make_submission_db(
        db,
        users=[{"callsign": "HB9A"}],
        stations=[{
            "callsign": "HB9A", "opname": "Anna", "submitted": True,
            "sta01bez": "FT-857", "sta01gramm": 1500, "gesamtegewicht": 1500,
        }],
        logs=[
            {"callsign": "HB9A", "utc": "0700", "remote_call": "HB9B",
             "rsts": "599", "rstr": "599"},
        ],
    )
    call_command("import_submission", str(db), "--year", "2026", stdout=StringIO())
    call_command("import_submission", str(db), "--year", "2026", stdout=StringIO())

    assert User.objects.filter(username="HB9A").count() == 1
    assert Participant.objects.filter(callsign="HB9A").count() == 1
    p = Participant.objects.get(callsign="HB9A")
    assert QsoEntry.objects.filter(participant=p).count() == 1
    assert StationComponent.objects.filter(participant=p).count() == 1


@pytest.mark.django_db
def test_logger_without_user_row_still_imports(seeded_contest, tmp_path):
    """A callsign that appears only in log_entry (no user row) still
    becomes a Participant — accounts get auto-created for it."""
    _run(
        tmp_path,
        users=[],
        stations=[],
        logs=[
            {"callsign": "HB9X", "utc": "0700", "remote_call": "HB9Y",
             "rsts": "599", "rstr": "599"},
        ],
    )
    assert User.objects.filter(username="HB9X").exists()
    p = Participant.objects.get(user__username="HB9X")
    assert QsoEntry.objects.filter(participant=p).count() == 1


@pytest.mark.django_db
def test_command_errors_when_year_not_seeded(seeded_contest, tmp_path):
    db = tmp_path / "submission.sqlite3"
    _make_submission_db(db, users=[{"callsign": "HB9A"}], stations=[], logs=[])
    with pytest.raises(CommandError, match="No contest with year=2099"):
        call_command("import_submission", str(db), "--year", "2099")


@pytest.mark.django_db
def test_command_errors_on_missing_file(seeded_contest, tmp_path):
    with pytest.raises(CommandError, match="Not a file"):
        call_command(
            "import_submission", str(tmp_path / "nope.sqlite3"), "--year", "2026",
        )


@pytest.mark.django_db
def test_command_errors_on_empty_db(seeded_contest, tmp_path):
    """All three tables empty → nothing to import; command refuses."""
    db = tmp_path / "submission.sqlite3"
    _make_submission_db(db)
    with pytest.raises(CommandError, match="no users, stations, or logs"):
        call_command("import_submission", str(db), "--year", "2026")

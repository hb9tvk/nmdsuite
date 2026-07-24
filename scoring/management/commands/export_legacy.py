"""Export a contest to a legacy TCL-scorer SQLite DB for cross-validation.

Inverse of :mod:`scoring.management.commands.import_legacy`. Reads the new
suite's ``Participant`` + ``QsoEntry`` rows and writes a SQLite file in the
legacy schema, so the old TCL scoring program can be run on the same data
and its ranking diffed against ours. Schema (see ``reference/scoring_tcl/``)::

    config (nmddate integer)                     -- created, left empty
    nmdlog (id, mode, localcall, dxcall, utc, rsts, rstr, txts, txtr,
            match, status, comment)
    nmdstn (nmdstn text, weight integer)

The population mirrors :func:`scoring.pairing.score_contest` exactly, so the
two scorers see the same input:

- **nmdstn** — every active (non-cancelled) participant: callsign with
  ``/P``, weight = ``Participant.total_weight_g`` (grams). Included even
  when the participant logged no QSOs, so the legacy scorer still treats
  them as a registered NMD station (which is what makes a peer's QSO with
  them ``UNMATCHED`` rather than ``HB9_QSO``).
- **nmdlog** — each active participant's *scorable* QSOs: those with a
  parsed ``utc_time``, a non-blank ``mode``, and a non-blank
  ``remote_call`` (the same three filters the engine applies). The
  referee-decision columns ``match`` / ``status`` / ``comment`` are set to
  ``0`` / ``0`` / ``''`` so the legacy scorer re-derives everything from
  the raw QSO data.

``utc`` reproduces the legacy convention: the QSO's UTC time-of-day mapped
onto 2000-01-01 as Unix epoch seconds (TCL ``clock scan 20000101THH:MM:00``).
That is exactly what ``import_legacy`` reads back with
``datetime.fromtimestamp(utc, UTC).strftime("%H%M")``, so an
export→import round-trip is loss-free for the fields that matter.

Usage::

    python manage.py export_legacy --year 2025 /path/to/legacy.db
    # then load legacy.db into the TCL scorer and compare rankings
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import Contest, Participant

# 2000-01-01T00:00:00Z. The legacy scorer anchors every QSO's time-of-day to
# this date, storing only HH:MM:00 (seconds are always zero).
_EPOCH_2000 = int(datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp())


class Command(BaseCommand):
    help = (
        "Export a contest to a legacy TCL-scorer SQLite DB (inverse of "
        "import_legacy) for cross-validating the ranking."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--year", type=int,
            help="Contest year (default: most recent non-archived).",
        )
        parser.add_argument("db_path", type=str, help="Output SQLite path.")
        parser.add_argument(
            "--force", action="store_true",
            help="Overwrite the output file if it already exists.",
        )

    def handle(self, *args, **opts) -> None:
        contest = self._resolve_contest(opts.get("year"))

        db_path = Path(opts["db_path"])
        if db_path.exists():
            if not opts["force"]:
                raise CommandError(f"{db_path} already exists — pass --force to overwrite.")
            db_path.unlink()

        participants = list(
            Participant.objects
            .filter(contest=contest, cancelled_at__isnull=True)
            .order_by("callsign")
        )

        conn = sqlite3.connect(str(db_path))
        try:
            self._create_schema(conn)
            n_stn = self._write_stations(conn, participants)
            n_log = self._write_qsos(conn, participants)
            conn.commit()
        finally:
            conn.close()

        self.stdout.write(self.style.SUCCESS(
            f"Wrote {db_path}: {n_stn} station(s), {n_log} QSO(s) from {contest}."
        ))

    # --- helpers ---------------------------------------------------------------------------

    @staticmethod
    def _resolve_contest(year: int | None) -> Contest:
        if year is not None:
            try:
                return Contest.objects.get(year=year)
            except Contest.DoesNotExist as exc:
                raise CommandError(f"No contest with year={year}") from exc
        contest = (
            Contest.objects
            .exclude(state=Contest.State.ARCHIVED)
            .order_by("-year")
            .first()
        )
        if contest is None:
            raise CommandError("No active contest found — pass --year explicitly.")
        return contest

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            "CREATE TABLE config (nmddate integer);"
            "CREATE TABLE nmdlog (id integer, mode text, localcall text, dxcall text, "
            "utc integer, rsts text, rstr text, txts text, txtr text, "
            "match integer, status integer, comment text);"
            "CREATE TABLE nmdstn (nmdstn text, weight integer);"
        )

    @staticmethod
    def _local_call(callsign: str) -> str:
        """NMD callsign in the legacy on-air form: uppercase, with ``/P``.

        Registered callsigns are stored bare (portable suffix stripped);
        the legacy scorer expects the ``/P``. Mirror import_legacy: append
        ``/P`` only when there is no suffix already, so prefixed calls like
        ``OE/HB9TVK`` are left untouched.
        """
        c = (callsign or "").strip().upper()
        return c if "/" in c else f"{c}/P"

    def _write_stations(self, conn: sqlite3.Connection, participants: list[Participant]) -> int:
        rows = [
            (self._local_call(p.callsign), int(p.total_weight_g or 0))
            for p in participants
        ]
        conn.executemany("INSERT INTO nmdstn (nmdstn, weight) VALUES (?, ?)", rows)
        return len(rows)

    def _write_qsos(self, conn: sqlite3.Connection, participants: list[Participant]) -> int:
        seq = 0
        rows = []
        for p in participants:
            localcall = self._local_call(p.callsign)
            qsos = (
                p.qsos
                .filter(utc_time__isnull=False)
                .exclude(mode="")
                .exclude(remote_call="")
                .order_by("utc_time", "id")
            )
            for q in qsos:
                seq += 1
                rows.append((
                    seq,
                    q.mode,
                    localcall,
                    (q.remote_call or "").strip(),
                    _legacy_utc(q.utc_time),
                    q.rsts, q.rstr, q.txts, q.txtr,
                    0, 0, "",
                ))
        conn.executemany(
            "INSERT INTO nmdlog (id, mode, localcall, dxcall, utc, "
            "rsts, rstr, txts, txtr, match, status, comment) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        return len(rows)


def _legacy_utc(dt: datetime) -> int:
    """QSO time-of-day mapped onto 2000-01-01 as Unix epoch seconds — the
    legacy scorer's convention (TCL ``clock scan 20000101THH:MM:00``)."""
    u = dt.astimezone(timezone.utc)
    return _EPOCH_2000 + u.hour * 3600 + u.minute * 60

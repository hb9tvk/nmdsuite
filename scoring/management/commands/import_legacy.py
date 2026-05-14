"""Bulk-import a legacy TCL scorer SQLite dump for M3 validation.

The legacy scoring program (``reference/scoring_tcl/``) stores all QSOs
in a single SQLite DB. Schema::

    config (nmddate integer)
    nmdlog (id, mode, localcall, dxcall, utc, rsts, rstr, txts, txtr,
            match, status, comment)
    nmdstn (nmdstn text, weight integer)

This command creates one ``Participant`` per unique ``localcall`` in
``nmdlog`` and loads their QSOs. ``utc`` is stored as Unix epoch seconds
in the legacy DB; we convert to ``HHMM`` and feed through the existing
``portal.qso_service.replace_qsos_from_upload`` so the same parsing,
mode-derivation, and validation paths run as for a portal upload.

Decisions encoded here:

- **Scope is engine validation only.** The legacy ``match`` / ``status`` /
  ``comment`` columns (the referee's manual scoring decisions) are NOT
  imported — the whole point of this exercise is to re-score the raw
  QSO data with our M3 engine and diff against the referee's output.
- **No tiebreaker data.** The ``nmdstn`` table (per-station weight) is
  ignored; weight is a tiebreaker, not a scoring input.
- **Date alignment is on the user.** ``config.nmddate`` is not verified
  against ``--year``; pass the year that matches the legacy contest.
- **/P convention.** Filename → callsign mapping doesn't apply here —
  the DB has ``localcall`` strings directly. ``/P`` is preserved if
  present, appended if absent (NMD all-stations-portable rule); use
  ``--no-portable-suffix`` for non-NMD test data.
- **Idempotent**: ``get_or_create`` for User+Participant + the existing
  atomic ``replace_qsos_from_upload``.

Usage::

    python manage.py seed_contest --year 2025 --force
    python manage.py import_legacy --year 2025 /path/to/legacy.db
    python manage.py run_scoring --year 2025 -v 2 > engine.txt
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Contest, Participant
from portal.qso_service import replace_qsos_from_upload
from registration.callsigns import normalize_callsign

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Bulk-import a legacy TCL scorer SQLite dump into a contest "
        "for M3 scoring engine validation."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, required=True, help="Target contest year.")
        parser.add_argument("db_path", type=str, help="Path to the legacy SQLite file.")
        parser.add_argument(
            "--no-portable-suffix",
            action="store_true",
            help="Do not append /P to Participant.callsign when missing.",
        )

    def handle(self, *args, **opts) -> None:
        year = opts["year"]
        try:
            contest = Contest.objects.get(year=year)
        except Contest.DoesNotExist as exc:
            raise CommandError(
                f"No contest with year={year} — seed it first with "
                f"`python manage.py seed_contest --year {year}`."
            ) from exc

        db_path = Path(opts["db_path"])
        if not db_path.is_file():
            raise CommandError(f"Not a file: {db_path}")

        append_p = not opts["no_portable_suffix"]

        conn = sqlite3.connect(str(db_path))
        try:
            conn.row_factory = sqlite3.Row
            localcalls = [
                row[0] for row in conn.execute(
                    "SELECT DISTINCT localcall FROM nmdlog "
                    "WHERE localcall IS NOT NULL AND TRIM(localcall) != '' "
                    "ORDER BY localcall"
                )
            ]
            if not localcalls:
                raise CommandError("Legacy DB has no rows in nmdlog.")

            imported = 0
            skipped = 0
            total_qsos = 0

            for localcall in localcalls:
                try:
                    rows = conn.execute(
                        "SELECT mode, dxcall, utc, rsts, rstr, txts, txtr "
                        "FROM nmdlog WHERE localcall = ? ORDER BY utc, id",
                        (localcall,),
                    ).fetchall()
                    count = self._import_participant(
                        contest, localcall, rows, append_p=append_p, db_name=db_path.name,
                    )
                    imported += 1
                    total_qsos += count
                    self.stdout.write(f"  + {localcall}: {count} QSOs")
                except (ValueError, sqlite3.Error) as exc:
                    self.stdout.write(self.style.WARNING(f"  ! {localcall}: {exc}"))
                    skipped += 1
        finally:
            conn.close()

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {imported} participant(s), {total_qsos} QSOs, "
                f"skipped {skipped}."
            )
        )

    @transaction.atomic
    def _import_participant(
        self,
        contest: Contest,
        localcall: str,
        rows: list[sqlite3.Row],
        *,
        append_p: bool,
        db_name: str,
    ) -> int:
        raw = normalize_callsign(localcall)
        if not raw:
            raise ValueError("empty localcall")
        # User.username is the bare callsign (no /P); Participant.callsign
        # preserves /P if present, appends it otherwise.
        username = raw.split("/")[0]
        if append_p and "/" not in raw:
            participant_call = f"{raw}/P"
        else:
            participant_call = raw

        user, _ = User.objects.get_or_create(
            username=username,
            defaults={
                "email": f"{username.lower()}@import.local",
                "first_name": username,
            },
        )
        participant, _ = Participant.objects.get_or_create(
            contest=contest,
            user=user,
            defaults={
                "callsign": participant_call,
                "first_name": username,
                "email": f"{username.lower()}@import.local",
                "coord_system_input": Participant.CoordSystem.CH1903PLUS,
                "coord_input_e": "",
                "coord_input_n": "",
                "ch1903p_e": 2_600_000,
                "ch1903p_n": 1_200_000,
                "wgs84_lat": 46.8,
                "wgs84_lon": 8.2,
                "altitude_m": 800,
                "canton": "ZH",
                "operating_modes": Participant.Mode.BOTH,
            },
        )

        qso_rows = [_row_to_upload_dict(r) for r in rows]
        # Filter rows where utc couldn't be turned into HHMM (NULL / bad epoch).
        qso_rows = [r for r in qso_rows if r is not None]
        return replace_qsos_from_upload(
            participant=participant,
            rows=qso_rows,
            filename=db_name,
        )


def _row_to_upload_dict(row: sqlite3.Row) -> dict[str, str] | None:
    """Convert one ``nmdlog`` row to the dict shape ``replace_qsos_from_upload``
    expects. Returns ``None`` for rows we can't make sense of."""
    utc = row["utc"]
    if utc is None:
        return None
    try:
        dt = datetime.fromtimestamp(int(utc), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return {
        "utc": dt.strftime("%H%M"),
        "remote_call": (row["dxcall"] or "").strip(),
        "rsts": (row["rsts"] or "").strip(),
        "txts": (row["txts"] or "").strip(),
        "rstr": (row["rstr"] or "").strip(),
        "txtr": (row["txtr"] or "").strip(),
    }

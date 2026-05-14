"""Bulk-import a legacy TCL scorer SQLite dump for M3 validation.

The legacy scoring program (``reference/scoring_tcl/``) stores all QSOs
in a single SQLite DB. Schema::

    config (nmddate integer)
    nmdlog (id, mode, localcall, dxcall, utc, rsts, rstr, txts, txtr,
            match, status, comment)
    nmdstn (nmdstn text, weight integer)

NMD station identity comes from ``nmdstn`` — that's the contest
registration set. We create one ``Participant`` per row there, then
load each one's QSOs from ``nmdlog`` (rows where ``localcall`` matches).
QSO rows in ``nmdlog`` whose ``localcall`` isn't registered are skipped
with a warning — they represent unregistered loggers, who per the
rules cannot be NMD stations and whose entries don't count.

This distinction matters: a remote callsign of HB9XYZ is classified as
``HB9_QSO`` (1 point, non-NMD) iff HB9XYZ isn't a registered NMD
station. If they registered but didn't log anything, our QSO with them
must be ``UNMATCHED`` (which the engine then promotes to
``SUSPECTED_CALL_MISMATCH`` if a peer's text matches). Reading from
``nmdstn`` is what keeps that distinction intact.

``utc`` is Unix epoch seconds; we convert to ``HHMM`` and feed through
``portal.qso_service.replace_qsos_from_upload`` so the same parsing,
mode-derivation, and validation paths run as for a portal upload.

Decisions encoded here:

- **Scope is engine validation only.** The legacy ``match`` / ``status`` /
  ``comment`` columns (the referee's manual scoring decisions) are NOT
  imported — the whole point of this exercise is to re-score the raw
  QSO data with our M3 engine and diff against the referee's output.
- **Tiebreaker weight ignored.** ``nmdstn.weight`` is a tiebreaker, not
  a scoring input; we read the callsigns but skip the weight.
- **Date alignment is on the user.** ``config.nmddate`` is not verified
  against ``--year``; pass the year that matches the legacy contest.
- **/P convention.** ``/P`` is preserved if present on the callsign,
  appended if absent (NMD all-stations-portable rule); use
  ``--no-portable-suffix`` for non-NMD test data.
- **nmdstn fallback.** If ``nmdstn`` is empty (older legacy dumps), we
  fall back to ``DISTINCT localcall FROM nmdlog`` and log a warning —
  the legacy semantics will be approximate in that case.
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
from scoring.pairing import match_key

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
            registered = self._read_registered_stations(conn)
            if not registered:
                self.stdout.write(self.style.WARNING(
                    "nmdstn is empty — falling back to DISTINCT localcall from nmdlog. "
                    "Unregistered loggers will be (incorrectly) treated as NMD stations."
                ))
                registered = self._distinct_loggers(conn)
            if not registered:
                raise CommandError("Legacy DB has neither nmdstn rows nor nmdlog rows.")

            # Set of normalized keys for fast 'is this localcall a registered NMD station?'.
            registered_keys = {match_key(c) for c in registered}

            imported = 0
            skipped_stations = 0
            unregistered_logs = 0
            total_qsos = 0

            for callsign in sorted(registered, key=match_key):
                try:
                    rows = conn.execute(
                        "SELECT mode, dxcall, utc, rsts, rstr, txts, txtr "
                        "FROM nmdlog WHERE localcall = ? ORDER BY utc, id",
                        (callsign,),
                    ).fetchall()
                    count = self._import_participant(
                        contest, callsign, rows, append_p=append_p, db_name=db_path.name,
                    )
                    imported += 1
                    total_qsos += count
                    suffix = f"{count} QSOs" if count else "no QSOs logged"
                    self.stdout.write(f"  + {callsign}: {suffix}")
                except (ValueError, sqlite3.Error) as exc:
                    self.stdout.write(self.style.WARNING(f"  ! {callsign}: {exc}"))
                    skipped_stations += 1

            # Surface any nmdlog entries with an unregistered localcall so the user
            # knows they're being dropped (this is rare but a useful sanity check).
            for row in conn.execute(
                "SELECT DISTINCT localcall FROM nmdlog "
                "WHERE localcall IS NOT NULL AND TRIM(localcall) != ''"
            ):
                raw = (row[0] or "").strip()
                if raw and match_key(raw) not in registered_keys:
                    unregistered_logs += 1
                    self.stdout.write(self.style.WARNING(
                        f"  ~ skipped unregistered logger: {raw}"
                    ))
        finally:
            conn.close()

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {imported} registered participant(s), {total_qsos} QSOs, "
                f"skipped {skipped_stations} station(s), "
                f"dropped {unregistered_logs} unregistered logger(s)."
            )
        )

    @staticmethod
    def _read_registered_stations(conn: sqlite3.Connection) -> list[str]:
        """Return non-empty callsigns from ``nmdstn``. Empty list if absent."""
        try:
            return [
                (row[0] or "").strip()
                for row in conn.execute(
                    "SELECT nmdstn FROM nmdstn "
                    "WHERE nmdstn IS NOT NULL AND TRIM(nmdstn) != ''"
                )
                if (row[0] or "").strip()
            ]
        except sqlite3.Error:
            # nmdstn table missing entirely — treat as empty so the fallback kicks in.
            return []

    @staticmethod
    def _distinct_loggers(conn: sqlite3.Connection) -> list[str]:
        """Fallback when ``nmdstn`` is empty: every distinct ``nmdlog.localcall``."""
        return [
            row[0] for row in conn.execute(
                "SELECT DISTINCT localcall FROM nmdlog "
                "WHERE localcall IS NOT NULL AND TRIM(localcall) != '' "
                "ORDER BY localcall"
            )
        ]

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

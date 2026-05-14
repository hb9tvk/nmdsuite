"""Bulk-import .nmd files into a contest for scoring validation.

Each ``*.nmd`` file in the target directory becomes one ``Participant`` +
log in the specified contest. The filename stem is taken as the
operator's callsign (the ``User.username``, no ``/P``); ``Participant.callsign``
gets ``/P`` appended per the NMD rule that all stations operate portable.

Decisions encoded here:

- **Scope is scoring validation only.** This command is a fixture
  loader, not the M4 admin "on-behalf registration" feature.
  Coordinates / altitude / canton are populated with placeholders
  (800 m, ZH, a generic CH1903+ point) because scoring is independent
  of station location. Real on-behalf registration with Swisstopo
  lookup, password setup, email confirmation etc. is M4's job.
- **Station-info comment lines from the .nmd are ignored.** They drive
  station weight (a tiebreaker, not a points input) and operator
  metadata; both are out of scope for engine validation.
- **Idempotent.** ``get_or_create`` for User + Participant, plus the
  existing atomic ``replace_qsos_from_upload`` for the QSO list, means
  re-running on the same directory cleanly rebuilds the contest state.
- **Per-file failure isolation.** A malformed file logs a warning and
  the loop continues, so one bad row doesn't block the rest of the
  validation set.

Usage::

    python manage.py seed_contest --year 2025 --force
    python manage.py import_logs --year 2025 /path/to/logs
    python manage.py run_scoring --year 2025 -v 2
"""
from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Contest, Participant
from portal.qso_service import replace_qsos_from_upload
from portal.qso_upload import UploadParseError, parse_upload
from registration.callsigns import normalize_callsign

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Bulk-import .nmd files into a contest for scoring validation. "
        "One file per participant; filename stem = callsign (without /P)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, required=True, help="Target contest year.")
        parser.add_argument("directory", type=str, help="Directory containing *.nmd files.")
        parser.add_argument(
            "--no-portable-suffix",
            action="store_true",
            help="Do not append /P to Participant.callsign (useful for non-NMD test data).",
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

        directory = Path(opts["directory"])
        if not directory.is_dir():
            raise CommandError(f"Not a directory: {directory}")

        files = sorted(directory.glob("*.nmd"))
        if not files:
            raise CommandError(f"No *.nmd files found in {directory}")

        append_p = not opts["no_portable_suffix"]
        imported = 0
        skipped = 0
        total_qsos = 0

        for f in files:
            try:
                count = self._import_one(contest, f, append_p=append_p)
                imported += 1
                total_qsos += count
                self.stdout.write(f"  + {f.name}: {count} QSOs")
            except (UploadParseError, ValueError, OSError) as exc:
                self.stdout.write(self.style.WARNING(f"  ! {f.name}: {exc}"))
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {imported} participant(s), {total_qsos} QSOs, "
                f"skipped {skipped} file(s)."
            )
        )

    @transaction.atomic
    def _import_one(self, contest: Contest, file: Path, *, append_p: bool) -> int:
        username = normalize_callsign(file.stem)
        if not username:
            raise ValueError("filename produced an empty callsign")
        callsign = f"{username}/P" if append_p else username

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
                "callsign": callsign,
                "first_name": username,
                "email": f"{username.lower()}@import.local",
                # Placeholders — scoring doesn't depend on these. M4 on-behalf
                # registration will do the real coordinate / Swisstopo dance.
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

        with file.open("rb") as fh:
            parsed = parse_upload(fh.read())

        return replace_qsos_from_upload(
            participant=participant,
            rows=parsed.qsos,
            filename=file.name,
        )

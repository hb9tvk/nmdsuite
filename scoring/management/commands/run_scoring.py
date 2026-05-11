"""Re-score every QSO in the active contest.

Usage::

    python manage.py run_scoring                  # most recent non-archived contest
    python manage.py run_scoring --year 2026      # explicit
    python manage.py run_scoring --year 2026 -v 2 # also print per-participant breakdown

Idempotent: ``score_contest`` wipes and rebuilds ``ScoringRecord`` rows
inside a single transaction. Admin overrides survive across runs.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.models import Contest
from scoring.pairing import score_contest
from scoring.totals import participant_breakdown


class Command(BaseCommand):
    help = "Re-score every QSO in a contest. Defaults to the most recent non-archived contest."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, help="Contest year (default: most recent non-archived).")

    def handle(self, *args, **opts) -> None:
        year = opts.get("year")
        if year is not None:
            try:
                contest = Contest.objects.get(year=year)
            except Contest.DoesNotExist as exc:
                raise CommandError(f"No contest with year={year}") from exc
        else:
            contest = (
                Contest.objects
                .exclude(state=Contest.State.ARCHIVED)
                .order_by("-year")
                .first()
            )
            if contest is None:
                raise CommandError("No active contest found — pass --year explicitly.")

        self.stdout.write(f"Scoring {contest}…")
        summary = score_contest(contest)

        total_qsos = sum(summary.values())
        for status, count in sorted(summary.items()):
            self.stdout.write(f"  {status}: {count}")
        self.stdout.write(self.style.SUCCESS(f"Done. {total_qsos} QSOs scored."))

        # Verbose mode: per-participant breakdown.
        if opts.get("verbosity", 1) >= 2:
            self.stdout.write("")
            self.stdout.write("Per-participant points (CW H1/H2 | SSB H1/H2 | total):")
            for p in contest.participants.filter(cancelled_at__isnull=True).order_by("callsign"):
                b = participant_breakdown(p)
                self.stdout.write(
                    f"  {p.callsign:<12} "
                    f"CW {b.cw.h1:>3}/{b.cw.h2:<3}  "
                    f"SSB {b.ssb.h1:>3}/{b.ssb.h2:<3}  "
                    f"total {b.total}"
                )

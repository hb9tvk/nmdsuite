"""Create (or refresh) a Contest row for a given year.

Usage::

    python manage.py seed_contest --year 2026
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timezone

from django.core.management.base import BaseCommand, CommandError

from core.models import Contest


def third_sunday_of_july(year: int) -> date:
    """Return the date of the 3rd Sunday in July for ``year``."""
    cal = calendar.Calendar(firstweekday=calendar.MONDAY)
    sundays = [d for d in cal.itermonthdates(year, 7) if d.month == 7 and d.weekday() == 6]
    return sundays[2]


class Command(BaseCommand):
    help = "Seed the database with a Contest row for the given year (third Sunday of July, 06–10 UTC)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument("--force", action="store_true", help="Overwrite an existing row.")

    def handle(self, *args, **opts) -> None:
        year = opts["year"]
        if year < 2000 or year > 2100:
            raise CommandError("Year out of supported range")

        contest_date = third_sunday_of_july(year)
        start = datetime.combine(contest_date, time(6, 0), tzinfo=timezone.utc)
        half = datetime.combine(contest_date, time(8, 0), tzinfo=timezone.utc)
        end = datetime.combine(contest_date, time(9, 59, 59), tzinfo=timezone.utc)

        existing = Contest.objects.filter(year=year).first()
        if existing and not opts["force"]:
            self.stdout.write(self.style.WARNING(f"Contest {year} already exists; use --force to overwrite."))
            return

        if existing:
            existing.contest_date = contest_date
            existing.start_utc = start
            existing.half_split_utc = half
            existing.end_utc = end
            existing.save()
            self.stdout.write(self.style.SUCCESS(f"Updated NMD {year} ({contest_date})."))
        else:
            Contest.objects.create(
                year=year,
                contest_date=contest_date,
                start_utc=start,
                half_split_utc=half,
                end_utc=end,
                state=Contest.State.REGISTRATION_OPEN,
            )
            self.stdout.write(self.style.SUCCESS(f"Seeded NMD {year} ({contest_date})."))

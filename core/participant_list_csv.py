"""Generate the participant list as CSV for logging-program import (F1).

Sister to :mod:`core.participant_list_pdf` — same data, plain-text format
expected by dedicated logging software.

Row format, one per active participant, ordered by callsign::

    <callsign-lowercase>/p,<first_name>,<east-LV03>,<north-LV03>,,,,

Notes:

- Eight comma-separated fields per row (four trailing empties).
- ``/p`` suffix is always appended; we store callsigns bare, the logging
  format expects portable. Country prefixes (``oe/hb9tvk``) are preserved.
- Coordinates in CH1903 / LV03 (six-digit). If a participant somehow has
  no canonical coords, the fields are left blank.
- ``\\n`` line terminator (not ``\\r\\n``) to match the sample.
- UTF-8 encoded so umlauts in operator names survive.
"""
from __future__ import annotations

import csv
import io

from .models import Contest, Participant


def build_participant_list_csv(contest: Contest) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")

    qs = (
        Participant.objects
        .filter(contest=contest, cancelled_at__isnull=True)
        .order_by("callsign")
    )
    for p in qs:
        callsign = f"{p.callsign.lower()}/p"
        east = "" if p.ch1903_e is None else str(p.ch1903_e)
        north = "" if p.ch1903_n is None else str(p.ch1903_n)
        writer.writerow([callsign, p.first_name, east, north, "", "", "", ""])
    return buf.getvalue().encode("utf-8")

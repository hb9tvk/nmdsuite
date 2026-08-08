"""Generate the pre-contest participant map as a PDF.

Sister to :mod:`core.participant_list_pdf` — same data, drawn instead of
tabulated. One circle per registered station at its own position on a
relief map of Switzerland, callsign printed beside it.

The legacy version was drawn by hand by a commission member after
registration closed. This one is regenerated on every download, so it is
always current.

All the drawing lives in :mod:`core.map_render`; this module only decides
which stations appear and what their labels say. The post-contest
counterpart is :mod:`public.ranking_map_pdf`.
"""
from __future__ import annotations

from .map_render import MapStation, render_station_map
from .models import Contest, Participant

# One artefact goes to every participant regardless of locale — see the
# note on hardcoded trilingual text in core.map_render.
SUBTITLES = (
    "Angemeldete Stationen",
    "Stations inscrites",
    "Stazioni iscritte",
)


def _stations(contest: Contest) -> list[MapStation]:
    """Every active registration that has usable coordinates.

    Registrations without coordinates are skipped rather than drawn at the
    origin — a circle in the Mediterranean is worse than a missing one.
    """
    qs = (
        Participant.objects
        .filter(contest=contest, cancelled_at__isnull=True)
        .order_by("callsign")
    )
    out: list[MapStation] = []
    for p in qs:
        if p.ch1903_e is None or p.ch1903_n is None:
            continue
        out.append(MapStation(label=p.callsign, east=p.ch1903_e, north=p.ch1903_n))
    return out


def build_participant_map_pdf(contest: Contest) -> bytes:
    """Render the active-participants map for ``contest`` as a PDF."""
    return render_station_map(
        year=contest.year,
        stations=_stations(contest),
        subtitles=SUBTITLES,
        doc_title=f"NMD {contest.year} — participant map",
    )

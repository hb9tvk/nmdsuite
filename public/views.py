"""Public ranking page (M4A.2).

Anonymous, year-indexed surface published once the contest reaches
``PUBLISHED`` (or ``ARCHIVED``). Previous-year contests stay reachable
at the same URL forever — that's the whole point of the year-in-URL
scheme.
"""
from __future__ import annotations

from dataclasses import asdict

from django.http import Http404, HttpResponse
from django.shortcuts import render

from core.models import Contest, Participant

from .ranking_service import (
    ANTENNA_LABEL,
    PSU_LABEL,
    TRX_LABEL,
    build_ranking_page,
)

_PUBLIC_STATES = (Contest.State.PUBLISHED, Contest.State.ARCHIVED)


def ranking(request, year: int) -> HttpResponse:
    """Render the combined ranking + station-data + map page for one
    contest year. 404 unless the contest is in a public state."""
    try:
        contest = Contest.objects.get(year=year)
    except Contest.DoesNotExist:
        raise Http404("No such contest year")
    if contest.state not in _PUBLIC_STATES:
        raise Http404("Results not yet published")

    page = build_ranking_page(contest)

    return render(
        request,
        "public/ranking.html",
        {
            "contest": contest,
            "page": page,
            # json_script tag in the template serialises this safely; passing
            # the list of dicts directly avoids attribute-quoting concerns.
            "markers": [asdict(m) for m in page.markers],
            "trx_label": TRX_LABEL,
            "psu_label": PSU_LABEL,
            "antenna_label": ANTENNA_LABEL,
        },
    )


def registrations(request) -> HttpResponse:
    """Public list of stations currently registered for the next NMD.

    Anonymous, sorted by callsign. Active (non-cancelled) participants
    only. Renders nothing useful when there's no upcoming contest —
    just an info banner."""
    contest = (
        Contest.objects
        .exclude(state__in=(Contest.State.PUBLISHED, Contest.State.ARCHIVED))
        .order_by("-year")
        .first()
    )
    if contest is None:
        return render(request, "public/registrations.html", {"contest": None})

    qs = (
        Participant.objects
        .filter(contest=contest, cancelled_at__isnull=True)
        .order_by("callsign")
    )
    rows = []
    for p in qs:
        if p.operating_modes == 3:
            modes = "CW+SSB"
        elif p.operating_modes == 1:
            modes = "CW"
        elif p.operating_modes == 2:
            modes = "SSB"
        else:
            modes = ""
        rows.append({
            "callsign": p.callsign,
            "first_name": p.first_name,
            "ch1903_e": p.ch1903_e,
            "ch1903_n": p.ch1903_n,
            "location_text": p.location_text,
            "canton": p.canton,
            "altitude_m": p.altitude_m,
            "modes": modes,
        })
    return render(
        request,
        "public/registrations.html",
        {"contest": contest, "rows": rows},
    )

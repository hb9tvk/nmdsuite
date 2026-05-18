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

from core.models import Contest

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

"""Staff-only scoring review (manual inspection / engine feel-out).

Two-pane layout: list of participants on the left with their CW/SSB
totals, full QSO log on the right for the currently selected one — each
row carrying a status badge, the matched peer's QSO inline (if any),
and any admin override / suspected-call diagnostic. Sits at
``/scoring/`` and ``/scoring/<participant_pk>/``.

This is intentionally a low-polish read-only page, scoped to "what did
the engine actually do?". It's not the participant-facing M2.6 view and
not part of the M4 admin module — both will land later.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, render

from core.models import Contest, Participant, QsoEntry, ScoringRecord
from scoring.totals import participant_breakdown


def _staff_required(view):
    return login_required(user_passes_test(lambda u: u.is_staff)(view))


def _resolve_contest(request) -> Contest | None:
    """Pick the contest to view. ``?year=YYYY`` wins if it points at a real
    row (archived ones included — we want to inspect old data too); otherwise
    default to the most recent non-archived contest."""
    raw_year = request.GET.get("year")
    if raw_year:
        try:
            return Contest.objects.get(year=int(raw_year))
        except (Contest.DoesNotExist, ValueError):
            return None
    return (
        Contest.objects
        .exclude(state=Contest.State.ARCHIVED)
        .order_by("-year")
        .first()
    )


def _row_for_participant(p: Participant) -> dict:
    """Sidebar entry: callsign + CW/SSB/total points."""
    b = participant_breakdown(p)
    return {
        "pk": p.pk,
        "callsign": p.callsign,
        "cw_total": b.cw.total,
        "ssb_total": b.ssb.total,
        "total": b.total,
    }


def _qso_with_score(qso: QsoEntry) -> dict:
    """Bundle a QSO with its ScoringRecord (or ``None``) for template rendering."""
    try:
        score = qso.score
    except ScoringRecord.DoesNotExist:
        score = None
    return {"qso": qso, "score": score}


@_staff_required
def review(request, pk: int | None = None):
    contest = _resolve_contest(request)
    all_years = list(Contest.objects.order_by("-year").values_list("year", flat=True))

    if contest is None:
        return render(request, "scoring/review.html", {
            "contest": None,
            "all_years": all_years,
        })

    participants = list(
        Participant.objects
        .filter(contest=contest, cancelled_at__isnull=True)
        .order_by("callsign")
    )
    participant_rows = [_row_for_participant(p) for p in participants]

    selected = None
    rows: list[dict] = []
    if pk is not None:
        selected = get_object_or_404(Participant, pk=pk, contest=contest)
        qsos = (
            QsoEntry.objects
            .filter(participant=selected)
            .select_related(
                "score",
                "score__matched_qso",
                "score__matched_qso__participant",
            )
            .order_by("utc_time", "utc_raw", "id")
        )
        rows = [_qso_with_score(q) for q in qsos]

    return render(request, "scoring/review.html", {
        "contest": contest,
        "all_years": all_years,
        "participants": participant_rows,
        "selected": selected,
        "rows": rows,
    })

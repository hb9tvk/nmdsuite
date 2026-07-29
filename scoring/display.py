"""Shared row-building for the scoring-review surfaces.

Both the staff review (:mod:`scoring.views`) and the participant-facing
``portal.views.scoring`` render one participant's QSO log with per-row
status. This centralises how those rows are assembled so both order them
identically and both handle out-of-window stragglers the same way.

A QSO logged just past the contest close (e.g. ``10:02``) is stored with
``utc_time = None`` by the permissive parser. Ordering by ``utc_time`` in
SQL then floats it to the TOP (NULL sorts first) and the template falls back
to the raw ``1002`` string. Here we reconstruct a transient timestamp for
such rows — exactly as the scorer does when pairing them — so they render as
``10:02`` and sort at the END, after the in-window rows.
"""
from __future__ import annotations

from core.models import Contest, Participant, QsoEntry, ScoringRecord

from .pairing import parse_clock


def _fill_display_time(qso: QsoEntry | None, contest: Contest) -> None:
    """Reconstruct a transient ``utc_time`` for a row the permissive parser
    left null because its logged time is just outside the contest window.
    Display/sort only — never saved. Genuinely unparseable times stay null
    and fall back to the raw string in the template."""
    if qso is not None and qso.utc_time is None and qso.utc_raw:
        qso.utc_time = parse_clock(qso.utc_raw, contest)


def scored_rows(participant: Participant, contest: Contest) -> list[dict]:
    """Return ``[{"qso", "score"}, ...]`` for ``participant``, ordered by
    time. Out-of-window stragglers get a reconstructed (transient) time so
    they sort at the end and render as ``HH:MM``; truly unparseable times
    keep ``utc_time = None`` and sort last of all."""
    qsos = list(
        QsoEntry.objects
        .filter(participant=participant)
        .select_related(
            "score",
            "score__matched_qso",
            "score__matched_qso__participant",
        )
    )
    rows: list[dict] = []
    for q in qsos:
        _fill_display_time(q, contest)
        try:
            score = q.score
        except ScoringRecord.DoesNotExist:
            score = None
        if score is not None:
            # The paired peer's QSO may itself be an out-of-window straggler.
            _fill_display_time(score.matched_qso, contest)
        rows.append({"qso": q, "score": score})

    # (utc_time is None) sorts the unparseable rows last; None == None makes
    # the tuple comparison skip past the datetime slot for those, so we never
    # compare None < None.
    rows.sort(key=lambda r: (
        r["qso"].utc_time is None,
        r["qso"].utc_time,
        r["qso"].utc_raw,
        r["qso"].id,
    ))
    return rows

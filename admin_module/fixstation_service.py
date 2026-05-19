"""Build the candidate list for the Fixstation Review surface (M4B).

Lists non-NMD remote callsigns that look suspicious — they were
logged by only 1 or 2 NMD participants, so a misheard / mistyped
callsign would go undetected without admin verification. Each
candidate gets external-lookup links (QRZ.com, QRZCQ, HamQTH) so
staff can sanity-check the call against the public databases.

Operates on what's in the QSO log directly (no dependence on a
scoring run). Cancelled participants are excluded.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from core.models import Contest, InvalidCallsign, Participant, QsoEntry
from registration.callsigns import core_callsign, normalize_callsign

# How many distinct NMD loggers a callsign can have and still be
# considered "suspicious enough" to surface. Three or more
# independent sightings is treated as corroborated.
_MAX_LOGGER_COUNT = 2


@dataclass(frozen=True)
class FixstationCandidate:
    callsign: str  # core form, used for the external-lookup URLs
    logger_count: int  # distinct NMD participants who logged this call
    logger_callsigns: list[str]  # for display
    is_invalid: bool  # whether currently flagged via InvalidCallsign


def build_candidates(contest: Contest) -> list[FixstationCandidate]:
    """Return the candidate list, sorted by logger_count ASC then
    callsign ASC (most suspicious first)."""
    registered_keys = {
        normalize_callsign(c).split("/")[0]
        for c in Participant.objects
        .filter(contest=contest, cancelled_at__isnull=True)
        .values_list("callsign", flat=True)
    }

    # Group QSO remote calls by core_callsign → set of NMD logger callsigns.
    loggers_by_call: dict[str, set[str]] = defaultdict(set)
    for qso in (
        QsoEntry.objects
        .filter(
            participant__contest=contest,
            participant__cancelled_at__isnull=True,
            participant__submitted_at__isnull=False,
        )
        .exclude(remote_call="")
        .select_related("participant")
    ):
        core = core_callsign(qso.remote_call)
        if not core:
            continue
        # Exclude NMD-registered callsigns; only suspicious non-NMD remotes.
        if core in registered_keys:
            continue
        loggers_by_call[core].add(qso.participant.callsign)

    flagged = set(
        InvalidCallsign.objects
        .filter(contest=contest)
        .values_list("callsign", flat=True)
    )

    out: list[FixstationCandidate] = []
    for call, loggers in loggers_by_call.items():
        if len(loggers) > _MAX_LOGGER_COUNT:
            continue
        out.append(FixstationCandidate(
            callsign=call,
            logger_count=len(loggers),
            logger_callsigns=sorted(loggers),
            is_invalid=call in flagged,
        ))
    # Most suspicious first (lowest logger count); callsign breaks ties.
    out.sort(key=lambda c: (c.logger_count, c.callsign))
    return out


def apply_invalid_flags(
    *, contest: Contest, marked_invalid: set[str], actor=None,
) -> tuple[int, int]:
    """Sync the contest's :class:`InvalidCallsign` table to match
    ``marked_invalid`` (the set of callsigns the admin ticked).

    Returns ``(added, removed)`` counts so the caller can surface a
    summary message. Callsigns the admin un-ticked are deleted;
    newly-ticked ones are inserted, attributed to ``actor``.
    """
    current = set(
        InvalidCallsign.objects
        .filter(contest=contest)
        .values_list("callsign", flat=True)
    )
    to_add = marked_invalid - current
    to_remove = current - marked_invalid

    if to_remove:
        InvalidCallsign.objects.filter(contest=contest, callsign__in=to_remove).delete()
    for call in to_add:
        InvalidCallsign.objects.create(contest=contest, callsign=call, flagged_by=actor)

    return len(to_add), len(to_remove)

"""Per-participant point breakdown (M3.5).

The ranking is published per mode (CW and SSB are separate lists, per
``NMDSuite.md`` §"Administration Module"); the per-half breakdown is
useful for the participant view (M2.6) so operators can see how their
two halves contributed.

Totals are computed JIT from ``ScoringRecord`` rows — there's no
aggregate table. Re-scoring is cheap enough that we don't need to cache
totals, and JIT keeps the data model simple. ``DUPE_DEDUCTED`` rows
contribute 0 points (assigned in :mod:`scoring.points`), so they fall
out of the sums naturally.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.models import Participant, QsoEntry, ScoringRecord


@dataclass(frozen=True)
class ModeBreakdown:
    h1: int = 0
    h2: int = 0

    @property
    def total(self) -> int:
        return self.h1 + self.h2


@dataclass(frozen=True)
class ParticipantBreakdown:
    cw: ModeBreakdown
    ssb: ModeBreakdown

    @property
    def total(self) -> int:
        return self.cw.total + self.ssb.total


def participant_breakdown(participant: Participant) -> ParticipantBreakdown:
    """Sum the participant's points by mode and half."""
    sums: dict[tuple[str, int], int] = {
        ("CW", 1): 0, ("CW", 2): 0,
        ("SSB", 1): 0, ("SSB", 2): 0,
    }
    rows = (
        ScoringRecord.objects
        .filter(qso__participant=participant)
        .values_list("qso__mode", "half", "points")
    )
    for mode, half, points in rows:
        bucket = sums.get((mode, half))
        if bucket is None:
            continue  # mode not in the CW/SSB set (e.g. blank) — already 0-point rows
        sums[(mode, half)] = bucket + points

    return ParticipantBreakdown(
        cw=ModeBreakdown(h1=sums[("CW", 1)], h2=sums[("CW", 2)]),
        ssb=ModeBreakdown(h1=sums[("SSB", 1)], h2=sums[("SSB", 2)]),
    )

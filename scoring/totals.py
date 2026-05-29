"""Per-participant point breakdown (M3.5).

The ranking is published per mode (CW and SSB are separate lists, per
``NMDSuite.md`` §"Administration Module"); the per-category breakdown is
useful for the participant view so operators can see how many NMD,
HB9-non-NMD, and DX QSOs they made in each mode.

Totals are computed JIT from ``ScoringRecord`` rows — there's no
aggregate table. Re-scoring is cheap enough that we don't need to cache
totals, and JIT keeps the data model simple. 0-point statuses
(``DUPE_DEDUCTED``, ``TEXT_MISMATCH``, ``UNMATCHED`` etc.) drop out of
the category counts naturally — only QSOs that actually scored show up.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.models import Participant, ScoringRecord, ScoringStatus


_NMD_STATUSES = frozenset({ScoringStatus.FULL_MATCH, ScoringStatus.ADMIN_ACCEPTED})


@dataclass(frozen=True)
class ModeBreakdown:
    nmd: int = 0
    hb9: int = 0
    dx: int = 0
    points: int = 0

    @property
    def qsos(self) -> int:
        return self.nmd + self.hb9 + self.dx


@dataclass(frozen=True)
class ParticipantBreakdown:
    cw: ModeBreakdown
    ssb: ModeBreakdown

    @property
    def nmd(self) -> int:
        return self.cw.nmd + self.ssb.nmd

    @property
    def hb9(self) -> int:
        return self.cw.hb9 + self.ssb.hb9

    @property
    def dx(self) -> int:
        return self.cw.dx + self.ssb.dx

    @property
    def qsos(self) -> int:
        return self.cw.qsos + self.ssb.qsos

    @property
    def points(self) -> int:
        return self.cw.points + self.ssb.points


def participant_breakdown(participant: Participant) -> ParticipantBreakdown:
    """Count the participant's scoring QSOs by mode and category, and
    sum the points per mode. 0-point statuses don't fall into any
    category and aren't counted, but their (zero) points pass through
    the sum harmlessly."""
    counts: dict[str, dict[str, int]] = {
        "CW": {"nmd": 0, "hb9": 0, "dx": 0, "points": 0},
        "SSB": {"nmd": 0, "hb9": 0, "dx": 0, "points": 0},
    }
    rows = (
        ScoringRecord.objects
        .filter(qso__participant=participant)
        .values_list("qso__mode", "status", "points")
    )
    for mode, status, points in rows:
        bucket = counts.get(mode)
        if bucket is None:
            continue  # mode not in the CW/SSB set (e.g. blank) — already 0-point rows
        bucket["points"] += points
        if status in _NMD_STATUSES:
            bucket["nmd"] += 1
        elif status == ScoringStatus.HB9_QSO:
            bucket["hb9"] += 1
        elif status == ScoringStatus.DX_QSO:
            bucket["dx"] += 1

    return ParticipantBreakdown(
        cw=ModeBreakdown(**counts["CW"]),
        ssb=ModeBreakdown(**counts["SSB"]),
    )

"""Points assignment (M3.5).

Per the contest rules (``reference_contest_rules.md``), an NMD↔NMD QSO is
worth 4 points and an NMD↔non-NMD QSO is worth 1 point. The status set
from M3.2–M3.4 maps directly onto a points value; this module is the
authoritative mapping.

Decisions encoded here:

- ``FULL_MATCH`` and ``ADMIN_ACCEPTED`` both pay 4. ``ADMIN_ACCEPTED`` is
  an admin's explicit override of an UNMATCHED / TEXT_MISMATCH row that
  they've decided counts as a clean NMD QSO.
- ``TEXT_MISMATCH`` pays 0 by default. The 2-error tolerance is already
  baked into FULL_MATCH (M3.2); a TEXT_MISMATCH means the receiver was
  off by more than 2 characters, which the established practice does
  *not* accept. Admin can override on a case-by-case basis.
- ``SUSPECTED_CALL_MISMATCH``, ``UNMATCHED`` and ``DUPE_DEDUCTED`` pay 0.
  The status is preserved for the participant view (M2.6) so they can
  see what happened.
- ``HB9_QSO`` and ``DX_QSO`` both pay 1. The rule is the same regardless
  of whether the non-participant station was Swiss or DX.
"""
from __future__ import annotations

from core.models import ScoringRecord, ScoringStatus


POINTS_BY_STATUS: dict[str, int] = {
    ScoringStatus.FULL_MATCH: 4,
    ScoringStatus.ADMIN_ACCEPTED: 4,
    ScoringStatus.HB9_QSO: 1,
    ScoringStatus.DX_QSO: 1,
    ScoringStatus.TEXT_MISMATCH: 0,
    ScoringStatus.UNMATCHED: 0,
    ScoringStatus.SUSPECTED_CALL_MISMATCH: 0,
    ScoringStatus.DUPE_DEDUCTED: 0,
}


def points_for(status: str) -> int:
    """Per-status points value. Unknown statuses default to 0 (defensive)."""
    return POINTS_BY_STATUS.get(status, 0)


def assign_points(records: list[ScoringRecord]) -> None:
    """Set ``points`` on every record according to its current ``status``.

    Called by ``score_contest`` after classification + suspected detection
    + override application + dupe deduction have all settled the final
    status. Mutates in place.
    """
    for r in records:
        r.points = points_for(r.status)

"""Apply admin-flagged invalid-callsign decisions (M4B).

After classification (``HB9_QSO`` / ``DX_QSO`` for non-NMD remotes),
check every flagged record's ``core_callsign`` against the per-contest
:class:`InvalidCallsign` set. Matches downgrade to ``INVALID_CALL`` —
worth 0 points (see ``scoring.points``). Pure read of the
:class:`InvalidCallsign` table; the admin module is the one that
writes it (M4B Fixstation Review view).
"""
from __future__ import annotations

from core.models import Contest, InvalidCallsign, ScoringRecord, ScoringStatus
from registration.callsigns import core_callsign

_NON_NMD_STATUSES = frozenset({ScoringStatus.HB9_QSO, ScoringStatus.DX_QSO})


def apply_invalid_callsigns(records: list[ScoringRecord], contest: Contest) -> int:
    """Downgrade matching non-NMD records to ``INVALID_CALL``. Returns
    the number of records that were touched.

    Only ``HB9_QSO`` and ``DX_QSO`` rows are candidates — the admin's
    "invalid" decision is about whether the contact happened at all,
    which only applies to QSOs that scored as non-NMD. NMD↔NMD
    classifications (matched / suspected / unmatched) stay as-is.
    """
    flagged = set(
        InvalidCallsign.objects
        .filter(contest=contest)
        .values_list("callsign", flat=True)
    )
    if not flagged:
        return 0

    touched = 0
    for r in records:
        if r.status not in _NON_NMD_STATUSES:
            continue
        if core_callsign(r.qso.remote_call) in flagged:
            r.status = ScoringStatus.INVALID_CALL
            touched += 1
    return touched

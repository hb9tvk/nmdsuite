"""Dupe deduction (M3.3).

Per the contest rules, two NMD stations may work each other at most once
per mode per half ("Zweitverbindungen") — up to two NMD↔NMD QSOs per
(peer, mode) pair across the contest are fine, but a second QSO in the
same half is a duplicate. The legacy TCL scorer only warned about
duplicates and required an admin to invalidate them by hand; this
module auto-deducts them, which is an explicit M3 enhancement
(``NMDSuite.md`` §"Scoring Module").

Decisions encoded here:

- **Bucket key**: ``(participant_id, peer_key, mode, half)``. The peer
  key is ``match_key(remote_call)`` — ``/P`` portable suffixes are
  stripped on both sides so they don't split a real dupe across two
  buckets.
- **Best-quality wins**, not earliest in time:
  ``FULL_MATCH > TEXT_MISMATCH > UNMATCHED``. Tie-break inside a status
  is earliest ``utc_time`` then lowest ``qso.id`` for determinism.
- **Only NMD statuses are dedupable here.** ``HB9_QSO`` and ``DX_QSO``
  have different dupe rules (once per mode for the whole contest,
  no half split) and are out of scope for this slice. ``DUPE_DEDUCTED``
  rows from a previous run are not present — ``score_contest`` calls us
  on a freshly classified record set, in the same transaction.
- **Losers keep their ``matched_qso``** pointer so admins can still see
  which peer QSO was the deduped one's pair. Only the ``status`` flips.
"""
from __future__ import annotations

from collections import defaultdict

from core.models import ScoringRecord, ScoringStatus

from .pairing import match_key


# Higher priority = kept over lower-priority rows in the same bucket.
# ADMIN_ACCEPTED ranks top because it's an explicit admin decision and
# must not be displaced by automatic classification.
# SUSPECTED_CALL_MISMATCH ranks above plain UNMATCHED because it carries
# extra diagnostic information (the operator's suspected real peer); it
# ranks below TEXT_MISMATCH because TEXT_MISMATCH has an actual confirmed
# peer QSO, while SUSPECTED only has a guess.
_NMD_PRIORITY: dict[str, int] = {
    ScoringStatus.ADMIN_ACCEPTED: 5,
    ScoringStatus.FULL_MATCH: 4,
    ScoringStatus.TEXT_MISMATCH: 3,
    ScoringStatus.SUSPECTED_CALL_MISMATCH: 2,
    ScoringStatus.UNMATCHED: 1,
}


def mark_dupes(records: list[ScoringRecord]) -> int:
    """Flip dupe rows in ``records`` to ``DUPE_DEDUCTED`` in place.

    Mutates the input list — caller is expected to persist the records
    afterward (``score_contest`` does this via ``bulk_create`` in the
    same transaction). Returns the number of rows that were flipped.
    """
    buckets: dict[tuple[int, str, str, int], list[ScoringRecord]] = defaultdict(list)
    for r in records:
        if r.status not in _NMD_PRIORITY:
            continue
        peer_key = match_key(r.qso.remote_call)
        if not peer_key:
            continue
        buckets[(r.qso.participant_id, peer_key, r.qso.mode, r.half)].append(r)

    flipped = 0
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        bucket.sort(key=lambda r: (
            -_NMD_PRIORITY[r.status],   # best status first
            r.qso.utc_time,             # earliest first within status
            r.qso.id,                   # deterministic tie-break
        ))
        for loser in bucket[1:]:
            loser.status = ScoringStatus.DUPE_DEDUCTED
            flipped += 1
    return flipped

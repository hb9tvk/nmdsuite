"""Dupe deduction.

Two distinct rules:

- **NMD↔NMD** ("Zweitverbindungen"): two NMD stations may work each
  other at most once per mode per half. Up to two NMD↔NMD QSOs per
  (peer, mode) pair across the contest are fine, but a second QSO in
  the *same half* is a duplicate. Bucket key:
  ``(participant, peer, mode, half)``. **Best-quality wins** —
  ``ADMIN_ACCEPTED > FULL_MATCH > TEXT_MISMATCH > SUSPECTED > UNMATCHED``,
  not chronologically earliest.
- **NMD↔non-NMD** (HB9_QSO / DX_QSO): once per (peer, mode) across the
  whole contest — no half split. Bucket key: ``(participant, peer, mode)``.
  All non-NMD QSOs pay 1 pt regardless, so there's no quality ordering;
  earliest ``utc_time`` wins.

Notes:

- The legacy TCL scorer only warned about duplicates and required an
  admin to invalidate them by hand; auto-deducting is an explicit M3
  enhancement (``NMDSuite.md`` §"Scoring Module").
- The peer key is ``match_key(remote_call)`` — ``/P`` portable suffixes
  are stripped on both sides so they don't split a real dupe across
  two buckets.
- Losers keep their ``matched_qso`` pointer so admins can still see
  which peer QSO was the deduped one's pair. Only the ``status`` flips.
- ``DUPE_DEDUCTED`` rows from a previous run are not present —
  ``score_contest`` calls us on a freshly classified record set, in the
  same transaction.
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

_NON_NMD_STATUSES = frozenset({
    ScoringStatus.HB9_QSO,
    ScoringStatus.DX_QSO,
})


def mark_dupes(records: list[ScoringRecord]) -> int:
    """Flip dupe rows in ``records`` to ``DUPE_DEDUCTED`` in place.

    Runs two passes — NMD (per (peer, mode, half), best-quality wins) and
    non-NMD (per (peer, mode), earliest wins). Mutates the input list;
    caller persists afterward (``score_contest`` does this via
    ``bulk_create`` in the same transaction). Returns the total number
    of rows that were flipped.
    """
    flipped = 0
    flipped += _mark_nmd_dupes(records)
    flipped += _mark_non_nmd_dupes(records)
    return flipped


def _mark_nmd_dupes(records: list[ScoringRecord]) -> int:
    """NMD↔NMD: bucket (participant, peer, mode, half); best-quality wins."""
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


def _mark_non_nmd_dupes(records: list[ScoringRecord]) -> int:
    """NMD↔non-NMD: bucket (participant, peer, mode); no half split. All
    non-NMD QSOs pay 1 pt regardless, so we don't quality-rank — earliest
    ``utc_time`` wins."""
    buckets: dict[tuple[int, str, str], list[ScoringRecord]] = defaultdict(list)
    for r in records:
        if r.status not in _NON_NMD_STATUSES:
            continue
        peer_key = match_key(r.qso.remote_call)
        if not peer_key:
            continue
        buckets[(r.qso.participant_id, peer_key, r.qso.mode)].append(r)

    flipped = 0
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        bucket.sort(key=lambda r: (r.qso.utc_time, r.qso.id))
        for loser in bucket[1:]:
            loser.status = ScoringStatus.DUPE_DEDUCTED
            flipped += 1
    return flipped

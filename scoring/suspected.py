"""Suspected-wrong-callsign detection (M3.4).

When an operator mis-hears the sender's callsign, the QSO ends up
``UNMATCHED``: the operator claims to have worked HB9ABC, but HB9ABC has
no QSO back at them. If at the same time *another* participant
transmitted a text that closely matches what the operator received,
that participant is plausibly the real sender — the operator just got
the callsign wrong. This module flags such rows as
``SUSPECTED_CALL_MISMATCH`` and records the suspected real callsign for
the participant to review (and for M4 admin to confirm / override).

Per ``NMDSuite.md`` §"Scoring Module": *"There will be no points given
in this case, but it would be helpful as information to the participant
to learn about the incorrect sender call sign."* — so the status change
here is purely diagnostic; M3.5 still grants 0 points.

Decisions encoded here:

- **Only ``UNMATCHED`` records are upgraded.** ``HB9_QSO`` / ``DX_QSO``
  rows already earn 1 point and the mishearing signal is weaker for
  non-participant callsigns; we'd rather not silently downgrade them.
  ``TEXT_MISMATCH`` already has a confirmed pair, so we don't second-guess.
- **Search universe**: all participants except (a) the operator
  themselves and (b) the participant they claimed to have worked.
- **Match criteria**: same mode, ``utc_time`` within
  :data:`~scoring.pairing.MATCH_WINDOW`, candidate's ``txts`` non-empty,
  ``text_distance(my_txtr, their_txts) <= DEFAULT_MAX_ERRORS``. We use
  the same 2-error tolerance as full-match pairing — if the operator
  was off by more than that, the signal is too weak to act on.
- **Best candidate**: smallest text-distance first, then smallest
  absolute time delta, then lowest ``qso.id`` for determinism.
- **``matched_qso`` is left ``None``** even when a suspect is identified;
  it's not a real match. ``suspected_correct_call`` carries the
  diagnostic.
"""
from __future__ import annotations

from core.models import Participant, QsoEntry, ScoringRecord, ScoringStatus

from .pairing import MATCH_WINDOW, match_key
from .text_match import DEFAULT_MAX_ERRORS, text_distance


def _find_suspected_sender(
    qso: QsoEntry,
    *,
    qsos_by_key: dict[str, list[QsoEntry]],
    my_key: str,
    exclude_key: str,
    max_errors: int = DEFAULT_MAX_ERRORS,
) -> tuple[str, QsoEntry] | None:
    """Return ``(participant_key, qso)`` of the best suspected real sender, or ``None``."""
    if not qso.txtr:
        return None
    lo = qso.utc_time - MATCH_WINDOW
    hi = qso.utc_time + MATCH_WINDOW

    best: tuple[int, float, int, str, QsoEntry] | None = None  # (dist, |dt|, id, key, qso)
    for key, peer_qsos in qsos_by_key.items():
        if key == my_key or key == exclude_key:
            continue
        for pq in peer_qsos:
            if pq.mode != qso.mode:
                continue
            if not (lo <= pq.utc_time <= hi):
                continue
            if not pq.txts:
                continue
            d = text_distance(qso.txtr, pq.txts)
            if d > max_errors:
                continue
            candidate = (d, abs((pq.utc_time - qso.utc_time).total_seconds()), pq.id, key, pq)
            if best is None or candidate < best:
                best = candidate
    if best is None:
        return None
    return best[3], best[4]


def detect_suspected(
    records: list[ScoringRecord],
    *,
    qsos_by_key: dict[str, list[QsoEntry]],
    participants_by_key: dict[str, Participant],
    key_by_participant_id: dict[int, str],
) -> int:
    """Flip ``UNMATCHED`` records to ``SUSPECTED_CALL_MISMATCH`` when a
    plausible mis-identified sender is found. Mutates in place. Returns
    the number of rows that were flipped."""
    flipped = 0
    for r in records:
        if r.status != ScoringStatus.UNMATCHED:
            continue
        qso = r.qso
        my_key = key_by_participant_id.get(qso.participant_id)
        if my_key is None:
            continue
        exclude_key = match_key(qso.remote_call)
        hit = _find_suspected_sender(
            qso,
            qsos_by_key=qsos_by_key,
            my_key=my_key,
            exclude_key=exclude_key,
        )
        if hit is None:
            continue
        suspected_key, _suspected_qso = hit
        r.status = ScoringStatus.SUSPECTED_CALL_MISMATCH
        r.suspected_correct_call = participants_by_key[suspected_key].callsign
        flipped += 1
    return flipped

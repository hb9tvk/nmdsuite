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

- **Eligible statuses**: ``UNMATCHED``, ``HB9_QSO``, and ``DX_QSO``. The
  last two cover the case where the operator typed a wrong callsign that
  happened to look Swiss or DX — the natural classification would be 1
  point for HB9/DX, but if the texts match a registered NMD station's
  transmission, this is much more likely a misheard NMD QSO (0 points,
  with a hint about who they probably actually worked). Legitimate
  HB9/DX QSOs exchange only RST per the rules, so they have empty
  ``txtr`` and aren't eligible for detection — false-positive risk is
  low.
- **``TEXT_MISMATCH`` needs stronger evidence**: such a row has a peer
  QSO claiming us, so single-direction similarity is not enough to
  second-guess it. But when another participant's QSO matches our texts
  in *both* directions, the "confirmed" pair was almost certainly a
  callsign mix-up on our side (we swapped two contacts' callsigns —
  the claim we're paired with belongs to a different QSO of ours).
  Flip to ``SUSPECTED_CALL_MISMATCH`` with the real sender, clear
  ``matched_qso`` (it was never our pair), and set ``text_distance``
  to the receiver-direction distance vs. the real sender so the stale
  mismatch distance doesn't mislead. Points stay 0 either way — the
  operator did log a wrong callsign — but the displayed *reason*
  becomes the true one.
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
from registration.callsigns import login_username

from .pairing import MATCH_WINDOW, match_key
from .text_match import DEFAULT_MAX_ERRORS, text_distance


_ELIGIBLE_STATUSES = frozenset({
    ScoringStatus.UNMATCHED,
    ScoringStatus.HB9_QSO,
    ScoringStatus.DX_QSO,
})


def _find_suspected_sender(
    qso: QsoEntry,
    *,
    qsos_by_key: dict[str, list[QsoEntry]],
    my_key: str,
    exclude_key: str,
    max_errors: int = DEFAULT_MAX_ERRORS,
    require_both_directions: bool = False,
) -> tuple[str, QsoEntry, int] | None:
    """Return ``(participant_key, qso, distance)`` of the best suspected
    real sender, or ``None``.

    ``require_both_directions`` additionally demands the candidate's
    *received* text match our *sent* text within ``max_errors`` — the
    stronger evidence needed to overturn a TEXT_MISMATCH, which (unlike
    UNMATCHED) has a peer QSO claiming us.
    """
    if not qso.txtr:
        return None
    if require_both_directions and not qso.txts:
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
            if require_both_directions:
                if not pq.txtr or text_distance(qso.txts, pq.txtr) > max_errors:
                    continue
            candidate = (d, abs((pq.utc_time - qso.utc_time).total_seconds()), pq.id, key, pq)
            if best is None or candidate < best:
                best = candidate
    if best is None:
        return None
    return best[3], best[4], best[0]


def detect_suspected(
    records: list[ScoringRecord],
    *,
    qsos_by_key: dict[str, list[QsoEntry]],
    participants_by_key: dict[str, Participant],
    key_by_participant_id: dict[int, str],
) -> int:
    """Flip records to ``SUSPECTED_CALL_MISMATCH`` when a plausible
    mis-identified sender is found. Mutates in place. Returns the number
    of rows that were flipped.

    ``UNMATCHED`` / ``HB9_QSO`` / ``DX_QSO`` rows flip on receiver-direction
    evidence alone; ``TEXT_MISMATCH`` rows have a peer QSO claiming us, so
    they flip only on the stronger both-directions evidence (see module
    docstring — the operator swapped two contacts' callsigns)."""
    flipped = 0
    for r in records:
        if r.status in _ELIGIBLE_STATUSES:
            require_both = False
        elif r.status == ScoringStatus.TEXT_MISMATCH:
            require_both = True
        else:
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
            require_both_directions=require_both,
        )
        if hit is None:
            continue
        suspected_key, _suspected_qso, distance = hit
        if r.status == ScoringStatus.TEXT_MISMATCH:
            # The strict claim we were paired with wasn't our QSO after
            # all — drop the pointer and replace the stale mismatch
            # distance with the one vs. the real sender.
            r.matched_qso = None
            r.text_distance = distance
        r.status = ScoringStatus.SUSPECTED_CALL_MISMATCH
        # Display the canonical on-air form (bare + /P), matching the
        # convention used by the pairing engine's mismatch detection.
        r.suspected_correct_call = (
            f"{login_username(participants_by_key[suspected_key].callsign)}/P"
        )
        flipped += 1
    return flipped

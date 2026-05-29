"""NMD↔NMD pairing engine (M3.2).

Walks every QSO in every active participant's log, finds the matching QSO
in the peer's log (if any), and classifies the result into a
:class:`core.models.ScoringStatus`. Persists one :class:`ScoringRecord`
per scored QSO. Points are NOT assigned here — that's M3.5. Dupe
deduction is NOT done here either — that's M3.3.

Decisions encoded here:

- Time window: a candidate QSO in the peer's log must fall within
  ``MATCH_WINDOW`` of ours (clocks drift). Set to ±10 minutes — the
  legacy TCL scorer used ±5 minutes but real-world validation showed
  that too many legitimate QSOs slipped through with the tighter
  window. See "Edge case at the half boundary" below for the dupe
  implication.
- **Edge case at the half boundary (08:00 UTC)**: with a ±10 min
  window, paired QSOs can straddle the H1/H2 split (e.g. 07:55 ↔
  08:05). If each side derived its own ``half`` from its own
  ``utc_time``, the same physical QSO would land in H1 on one side
  and H2 on the other; an operator who also worked the peer in
  H2 cleanly would then get their boundary QSO deducted as a dupe
  on one side but not the other. To keep both sides consistent
  for dupe purposes, ``_qso_half`` derives the half from the
  **earlier** of the paired pair's timestamps when a mate exists,
  so both ScoringRecords agree.
- Callsign normalisation: ``/P``/``/M``/``/MM`` portable suffixes are
  stripped on both sides before comparing — operators are inconsistent
  about typing the suffix into the remote-call field. Uses the same
  helper the login flow uses (``registration.callsigns.login_username``).
- Text tolerance: up to 2 character errors on the receiver side still
  counts as a full match (see ``scoring.text_match.DEFAULT_MAX_ERRORS``).
  The comparison is **asymmetric on purpose**: we can't tell whether a
  discrepancy is the sender keying the text wrong or the receiver
  mis-hearing it, so we assume the sender is correct and always charge
  the error to the receiver. Each QSO is therefore classified on its
  own receiver direction (my ``txtr`` vs. the peer's ``txts``);
  the peer's record is judged independently on *their* receiver
  direction. The two sides of one QSO pair may end up with different
  statuses — that's the rule, not a bug.
- Non-participant remotes: classified as ``HB9_QSO`` (Swiss prefixes
  HB9*/HB3*/HE*) or ``DX_QSO`` (anything else). No pairing attempted.
- Skipped rows: QSOs with null ``utc_time``, blank ``mode``, or blank
  ``remote_call`` get no ``ScoringRecord`` at all — they're not valid
  enough to score and would only confuse downstream totals.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction

from core.models import (
    Contest,
    Participant,
    QsoEntry,
    ScoringRecord,
    ScoringStatus,
)
from registration.callsigns import login_username, normalize_callsign

from .text_match import DEFAULT_MAX_ERRORS, text_distance
# Imported below at call-time to avoid an import cycle (dupes imports match_key from us).


MATCH_WINDOW = timedelta(minutes=10)

# Swiss callsign prefixes per the contest rules; used to distinguish HB9_QSO
# (Swiss but non-participant) from DX_QSO (everything else).
SWISS_PREFIXES = ("HB9", "HB3", "HE")


@dataclass(frozen=True)
class Classification:
    """Pure-function output of :func:`classify_qso`. No DB state."""

    status: ScoringStatus
    matched_qso: QsoEntry | None
    text_distance: int
    suspected_correct_call: str = ""


def match_key(callsign: str) -> str:
    """Form used to compare callsigns ignoring /P, /M, /MM portable suffixes."""
    return login_username(callsign)


def is_swiss_callsign(call: str) -> bool:
    norm = normalize_callsign(call)
    return any(norm.startswith(p) for p in SWISS_PREFIXES)


def _receiver_distance(qso: QsoEntry, mate: QsoEntry) -> int:
    """Receiver-side distance for ``qso``: how many characters the local
    operator mis-received vs. what the remote claims to have sent.

    The sender's transmission is assumed correct — when ``qso.txtr`` and
    ``mate.txts`` disagree we can't tell whether the sender keyed it wrong
    or the receiver mis-heard, so we always charge the receiver. The
    peer's QSO is judged independently on the other direction.
    """
    return text_distance(qso.txtr, mate.txts)


def _is_fuzzy_pair(qso: QsoEntry, candidate: QsoEntry, max_errors: int) -> bool:
    """Both directions of text within tolerance — strong evidence the
    candidate is the peer's record of *our* QSO, even though they typed
    a wrong dxcall. We require BOTH directions to match (not just the
    receiver direction) because we don't have the callsign as evidence
    here; text similarity is the only signal, so we want it on both
    sides to avoid false-positive matches against the peer's QSOs with
    other operators in the same time window.
    """
    if not (qso.txtr and qso.txts and candidate.txts and candidate.txtr):
        return False
    return (
        text_distance(qso.txtr, candidate.txts) <= max_errors
        and text_distance(qso.txts, candidate.txtr) <= max_errors
    )


def _best_candidate(qso: QsoEntry, candidates: list[QsoEntry]) -> QsoEntry | None:
    """Closest-in-time wins. Ties broken by id for deterministic scoring."""
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda c: (abs((c.utc_time - qso.utc_time).total_seconds()), c.id),
    )


def classify_qso(
    qso: QsoEntry,
    *,
    peer_qsos: list[QsoEntry] | None,
    my_key: str | None = None,
    peer_callsign: str | None = None,
    max_errors: int = DEFAULT_MAX_ERRORS,
) -> Classification:
    """Classify a single QSO.

    ``peer_qsos`` is the peer's full log (the entire list of their QSOs).
    ``None`` means the remote isn't a registered NMD station — classify as
    HB9_QSO or DX_QSO, no pairing attempted.

    ``my_key`` is the local participant's normalised callsign (output of
    :func:`match_key`). Used to find the *strict* pairing candidate (peer
    recorded us as their remote). If ``None`` is passed, no strict filter
    is applied — a test-friendly default so direct invocations can pass a
    pre-filtered candidate list.

    ``peer_callsign`` is the peer's registered ``Participant.callsign``
    (with ``/P`` etc. preserved). When provided, we verify our typed
    dxcall matches it exactly (modulo case/whitespace). The peer lookup
    itself is portable-suffix-tolerant (so we can find HB9TVK/P when the
    operator typed HB9TVK), but giving 4 points requires the operator to
    have typed the callsign as the station registered it. A mismatch —
    e.g. missing ``/P`` on the dxcall when the peer is registered as
    ``HB3YMQ/P`` — downgrades to ``SUSPECTED_CALL_MISMATCH``. The peer's
    registered callsign goes into ``suspected_correct_call``.

    Two-stage pairing:

    - **Strict**: peer's recorded dxcall normalises to ``my_key``. Receiver-
      direction text-distance decides FULL_MATCH vs TEXT_MISMATCH (the
      sender is always assumed correct; see :func:`_receiver_distance`).
    - **Fuzzy**: peer's dxcall doesn't match us, but their texts match ours
      *in both directions* within ``max_errors``. Strong evidence the peer
      typed a wrong dxcall for our QSO. Classified as FULL_MATCH — we got
      the call right, the peer's typo isn't our problem.
    """
    if peer_qsos is None:
        status = ScoringStatus.HB9_QSO if is_swiss_callsign(qso.remote_call) else ScoringStatus.DX_QSO
        return Classification(status=status, matched_qso=None, text_distance=0)

    lo = qso.utc_time - MATCH_WINDOW
    hi = qso.utc_time + MATCH_WINDOW
    in_window = [c for c in peer_qsos if c.mode == qso.mode and lo <= c.utc_time <= hi]
    if not in_window:
        return Classification(status=ScoringStatus.UNMATCHED, matched_qso=None, text_distance=0)

    # The canonical on-air callsign for an NMD station is the bare callsign
    # plus /P (NMD is a portable contest). Registered callsigns are stored
    # bare; operators are expected to type them with the /P suffix on air
    # and in the log. Missing /P (or any other typo) costs them the points.
    expected_on_air = f"{login_username(peer_callsign)}/P" if peer_callsign else None
    call_mismatch = (
        expected_on_air is not None
        and normalize_callsign(qso.remote_call) != normalize_callsign(expected_on_air)
    )

    # Stage 1 — strict: peer recorded us as their remote.
    if my_key is not None:
        strict_candidates = [c for c in in_window if match_key(c.remote_call) == my_key]
    else:
        strict_candidates = in_window  # test-friendly: caller pre-filtered or doesn't care
    mate = _best_candidate(qso, strict_candidates)
    if mate is not None:
        has_texts = bool(qso.txtr) and bool(mate.txts)
        distance = _receiver_distance(qso, mate)
        if call_mismatch:
            return Classification(
                status=ScoringStatus.SUSPECTED_CALL_MISMATCH,
                matched_qso=mate,
                text_distance=distance,
                suspected_correct_call=expected_on_air or "",
            )
        if has_texts and distance <= max_errors:
            return Classification(status=ScoringStatus.FULL_MATCH, matched_qso=mate, text_distance=distance)
        return Classification(status=ScoringStatus.TEXT_MISMATCH, matched_qso=mate, text_distance=distance)

    # Stage 2 — fuzzy: peer typed a wrong dxcall but their texts match ours both ways.
    fuzzy_candidates = [c for c in in_window if _is_fuzzy_pair(qso, c, max_errors)]
    if fuzzy_candidates:
        best = min(fuzzy_candidates, key=lambda c: (
            _receiver_distance(qso, c),
            abs((c.utc_time - qso.utc_time).total_seconds()),
            c.id,
        ))
        distance = _receiver_distance(qso, best)
        if call_mismatch:
            return Classification(
                status=ScoringStatus.SUSPECTED_CALL_MISMATCH,
                matched_qso=best,
                text_distance=distance,
                suspected_correct_call=expected_on_air or "",
            )
        return Classification(
            status=ScoringStatus.FULL_MATCH,
            matched_qso=best,
            text_distance=distance,
        )

    return Classification(status=ScoringStatus.UNMATCHED, matched_qso=None, text_distance=0)


def _qso_half(qso: QsoEntry, contest: Contest, *, mate: QsoEntry | None = None) -> int:
    """Half of the contest a QSO belongs to (1 = pre-split, 2 = post-split).

    When a paired ``mate`` is provided, use the EARLIER of the two
    timestamps so both sides of a boundary-crossing pair land in the
    same half. Keeps dupe deduction symmetric near 08:00 UTC where the
    ±10 min match window straddles the split.
    """
    t = qso.utc_time
    if mate is not None and mate.utc_time is not None:
        t = min(t, mate.utc_time)
    return 1 if t < contest.half_split_utc else 2


@transaction.atomic
def score_contest(contest: Contest) -> dict[str, int]:
    """Re-classify every QSO in ``contest``.

    Wipes existing ``ScoringRecord`` rows for this contest and rebuilds them
    from scratch. Returns a ``{status: count}`` summary. Cancelled
    participants are excluded. Rows with no ``utc_time`` / ``mode`` /
    ``remote_call`` are silently skipped — they're permissive saves that
    aren't ready to score.

    Pipeline order: classify → detect suspected → apply overrides →
    mark dupes → assign points → persist. Overrides run before dedupe so
    admin decisions outrank automatic classification when picking the
    bucket winner; points are assigned last so they reflect the final
    status (DUPE_DEDUCTED rows naturally fall to 0).
    """
    from .dupes import mark_dupes
    from .invalid_callsigns import apply_invalid_callsigns
    from .overrides import apply_overrides
    from .points import assign_points
    from .suspected import detect_suspected

    participants = list(
        Participant.objects
        .filter(contest=contest, cancelled_at__isnull=True)
    )
    # Map: callsign-without-/P → Participant / list of that participant's scorable QSOs.
    participants_by_key: dict[str, Participant] = {match_key(p.callsign): p for p in participants}
    key_by_participant_id: dict[int, str] = {p.id: k for k, p in participants_by_key.items()}
    qsos_by_key: dict[str, list[QsoEntry]] = {
        k: list(
            p.qsos
            .filter(utc_time__isnull=False)
            .exclude(mode="")
            .exclude(remote_call="")
            .order_by("utc_time", "id")
        )
        for k, p in participants_by_key.items()
    }

    records: list[ScoringRecord] = []
    for p_key, p in participants_by_key.items():
        for qso in qsos_by_key[p_key]:
            remote_key = match_key(qso.remote_call)
            peer_qsos: list[QsoEntry] | None = None
            peer_callsign: str | None = None
            if remote_key and remote_key != p_key and remote_key in qsos_by_key:
                # Pass the peer's full log; classify_qso does strict + fuzzy filtering.
                peer_qsos = qsos_by_key[remote_key]
                peer_callsign = participants_by_key[remote_key].callsign
            result = classify_qso(
                qso, peer_qsos=peer_qsos, my_key=p_key, peer_callsign=peer_callsign,
            )
            records.append(ScoringRecord(
                qso=qso,
                status=result.status,
                matched_qso=result.matched_qso,
                text_distance=result.text_distance,
                half=_qso_half(qso, contest, mate=result.matched_qso),
                suspected_correct_call=result.suspected_correct_call,
            ))

    # Order matters: detect suspected BEFORE invalid-callsign BEFORE
    # overrides BEFORE dedupe. A SUSPECTED upgrade can win over plain
    # UNMATCHED in dedup. INVALID_CALL downgrades non-NMD before admin
    # overrides get a chance to put a specific QSO back on the books.
    detect_suspected(
        records,
        qsos_by_key=qsos_by_key,
        participants_by_key=participants_by_key,
        key_by_participant_id=key_by_participant_id,
    )
    apply_invalid_callsigns(records, contest)
    apply_overrides(records, contest)
    mark_dupes(records)
    assign_points(records)
    ScoringRecord.objects.filter(qso__participant__contest=contest).delete()
    ScoringRecord.objects.bulk_create(records)

    summary: dict[str, int] = defaultdict(int)
    for r in records:
        summary[r.status] += 1
    return dict(summary)

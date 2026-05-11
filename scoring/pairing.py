"""NMD↔NMD pairing engine (M3.2).

Walks every QSO in every active participant's log, finds the matching QSO
in the peer's log (if any), and classifies the result into a
:class:`core.models.ScoringStatus`. Persists one :class:`ScoringRecord`
per scored QSO. Points are NOT assigned here — that's M3.5. Dupe
deduction is NOT done here either — that's M3.3.

Decisions encoded here:

- Time window: a candidate QSO in the peer's log must fall within
  ``MATCH_WINDOW`` of ours (clocks drift). Mirrors the legacy TCL
  scorer's ±5 minute window (``reference/scoring_tcl/cgi-bin/nmdaw.wsh``).
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


MATCH_WINDOW = timedelta(minutes=5)

# Swiss callsign prefixes per the contest rules; used to distinguish HB9_QSO
# (Swiss but non-participant) from DX_QSO (everything else).
SWISS_PREFIXES = ("HB9", "HB3", "HE")


@dataclass(frozen=True)
class Classification:
    """Pure-function output of :func:`classify_qso`. No DB state."""

    status: ScoringStatus
    matched_qso: QsoEntry | None
    text_distance: int


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
    max_errors: int = DEFAULT_MAX_ERRORS,
) -> Classification:
    """Classify a single QSO. ``peer_qsos`` is the peer's full log filtered
    to QSOs *back at us*; ``None`` means the remote isn't a participant."""
    if peer_qsos is None:
        status = ScoringStatus.HB9_QSO if is_swiss_callsign(qso.remote_call) else ScoringStatus.DX_QSO
        return Classification(status=status, matched_qso=None, text_distance=0)

    lo = qso.utc_time - MATCH_WINDOW
    hi = qso.utc_time + MATCH_WINDOW
    in_window = [c for c in peer_qsos if c.mode == qso.mode and lo <= c.utc_time <= hi]
    mate = _best_candidate(qso, in_window)
    if mate is None:
        return Classification(status=ScoringStatus.UNMATCHED, matched_qso=None, text_distance=0)

    # FULL_MATCH requires the two text fields relevant to *this* receiver
    # direction to both be non-empty. An empty-vs-empty pair has distance 0
    # trivially, but that isn't a real NMD QSO; and a missing claimed-sent
    # text from the peer means we have no ground truth to validate against.
    has_texts = bool(qso.txtr) and bool(mate.txts)
    distance = _receiver_distance(qso, mate)
    if has_texts and distance <= max_errors:
        return Classification(status=ScoringStatus.FULL_MATCH, matched_qso=mate, text_distance=distance)
    return Classification(status=ScoringStatus.TEXT_MISMATCH, matched_qso=mate, text_distance=distance)


def _qso_half(qso: QsoEntry, contest: Contest) -> int:
    return 1 if qso.utc_time < contest.half_split_utc else 2


@transaction.atomic
def score_contest(contest: Contest) -> dict[str, int]:
    """Re-classify every QSO in ``contest``.

    Wipes existing ``ScoringRecord`` rows for this contest and rebuilds them
    from scratch. Returns a ``{status: count}`` summary. Cancelled
    participants are excluded. Rows with no ``utc_time`` / ``mode`` /
    ``remote_call`` are silently skipped — they're permissive saves that
    aren't ready to score.

    M3.5 (points + overrides) will layer on top of the classification +
    suspected detection + dupe deduction left here.
    """
    from .dupes import mark_dupes
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
            if remote_key and remote_key != p_key and remote_key in qsos_by_key:
                # Only their QSOs back at us are candidates.
                peer_qsos = [c for c in qsos_by_key[remote_key] if match_key(c.remote_call) == p_key]
            result = classify_qso(qso, peer_qsos=peer_qsos)
            records.append(ScoringRecord(
                qso=qso,
                status=result.status,
                matched_qso=result.matched_qso,
                text_distance=result.text_distance,
                half=_qso_half(qso, contest),
            ))

    # Order matters: detect suspected BEFORE dedupe so a SUSPECTED row can
    # win over a plain UNMATCHED in the same bucket.
    detect_suspected(
        records,
        qsos_by_key=qsos_by_key,
        participants_by_key=participants_by_key,
        key_by_participant_id=key_by_participant_id,
    )
    mark_dupes(records)
    ScoringRecord.objects.filter(qso__participant__contest=contest).delete()
    ScoringRecord.objects.bulk_create(records)

    summary: dict[str, int] = defaultdict(int)
    for r in records:
        summary[r.status] += 1
    return dict(summary)

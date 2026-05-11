"""Admin override reattachment (M3.5).

``ScoringOverride`` rows persist across re-scoring runs (per the model's
docstring: "Keyed loosely (participant + UTC + remote_call + mode) rather
than by QSO PK, so that re-imports of the participant's log reattach old
decisions"). This module is the reattachment step: after classification,
suspected detection, and *before* dupe deduction, any matching override
forces its ``forced_status`` onto the record and stamps
``admin_overridden`` + ``admin_comment``.

Decisions encoded here:

- **Order in the pipeline**: overrides run *before* ``mark_dupes``, so an
  ``ADMIN_ACCEPTED`` row outranks plain pairing results in the same
  bucket (it gets the top priority in ``_NMD_PRIORITY``). An admin
  forcing ``DUPE_DEDUCTED`` removes the row from dedupe consideration
  entirely (it's already a loser).
- **Loose key**: the override's ``remote_call`` is normalised via
  :func:`scoring.pairing.match_key` before matching, so an override
  written against ``HB9ABC`` still applies after the participant re-uploads
  the log as ``HB9ABC/P``. Same on the QSO side. When two overrides
  collide after normalisation (very rare — admin would have to have
  created both manually), the most recently decided one wins.
- **No write side**: this module only *applies* overrides. Creating
  overrides is the admin module's job (M4).
"""
from __future__ import annotations

from core.models import Contest, ScoringOverride, ScoringRecord

from .pairing import match_key


def apply_overrides(records: list[ScoringRecord], contest: Contest) -> int:
    """Apply every matching ``ScoringOverride`` to ``records`` in place.
    Returns the number of records that were touched."""
    overrides_by_key: dict[tuple[int, object, str, str], ScoringOverride] = {}
    for o in (
        ScoringOverride.objects
        .filter(participant__contest=contest)
        .order_by("decided_at")
    ):
        key = (o.participant_id, o.utc_time, match_key(o.remote_call), o.mode)
        overrides_by_key[key] = o  # later decided_at wins on collision

    touched = 0
    for r in records:
        qso = r.qso
        if qso.utc_time is None or not qso.mode:
            continue
        key = (qso.participant_id, qso.utc_time, match_key(qso.remote_call), qso.mode)
        override = overrides_by_key.get(key)
        if override is None:
            continue
        r.status = override.forced_status
        r.admin_overridden = True
        r.admin_comment = override.comment
        touched += 1
    return touched

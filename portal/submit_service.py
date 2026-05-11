"""Finalise-and-lock action for a participant's log (M2.5).

The operator clicks "Submit log" on the dashboard. Submission is permissive
— we don't refuse based on invalid QSO rows or over-weight stations; the
operator decides what to file. Once submitted, every editing surface in
the portal becomes read-only (the views consult ``participant.submitted_at``).

This is a one-way action by design. If a participant needs to amend a
submitted log, an admin override is required (M4 admin module).
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.audit import audit
from core.models import Participant

from .emails import send_log_submitted_confirmation


@transaction.atomic
def submit_log(*, participant: Participant) -> Participant:
    """Mark the participant's log as submitted. No-op if already submitted."""
    if participant.submitted_at is not None:
        return participant

    participant.submitted_at = timezone.now()
    participant.save(update_fields=["submitted_at"])

    station = getattr(participant, "station", None)
    audit(
        action="log.submit",
        actor=participant.user,
        target=participant.callsign,
        contest=participant.contest,
        payload={
            "qso_count": participant.qsos.count(),
            "total_weight_g": station.total_weight_g if station is not None else 0,
        },
    )

    send_log_submitted_confirmation(participant=participant)
    return participant

"""Finalise-and-lock action for a participant's log (M2.5).

The operator clicks "Submit log" on the dashboard. Submission is permissive
— we don't refuse based on invalid QSO rows or over-weight stations; the
operator decides what to file. Once submitted, every editing surface in
the portal becomes read-only (the views consult ``participant.submitted_at``).

For the operator this is a one-way action; if they need to amend a
submitted log, an admin override is required. The M4 admin module supplies
that override via :func:`release_log`.
"""
from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from core.audit import audit
from core.models import Participant

from .emails import send_log_submitted_confirmation


@transaction.atomic
def submit_log(*, participant: Participant, actor: Any = None) -> Participant:
    """Mark the participant's log as submitted. No-op if already submitted.

    ``actor`` defaults to the participant's own user (portal self-submit).
    Admin on-behalf submits pass in the staff user; the audit row records
    them with ``on_behalf=True`` and the confirmation email is skipped (the
    operator did not trigger this action; admin can follow up manually).
    """
    if participant.submitted_at is not None:
        return participant

    participant.submitted_at = timezone.now()
    participant.save(update_fields=["submitted_at"])

    station = getattr(participant, "station", None)
    is_on_behalf = actor is not None and actor != participant.user
    audit_payload: dict[str, Any] = {
        "qso_count": participant.qsos.count(),
        "total_weight_g": station.total_weight_g if station is not None else 0,
    }
    if is_on_behalf:
        audit_payload["on_behalf"] = True
    audit(
        action="log.submit",
        actor=actor or participant.user,
        target=participant.callsign,
        contest=participant.contest,
        payload=audit_payload,
    )

    if not is_on_behalf:
        send_log_submitted_confirmation(participant=participant)
    return participant


@transaction.atomic
def release_log(*, participant: Participant, actor: Any) -> Participant:
    """Un-submit a previously-submitted log so the operator can edit again.

    Admin-only — there is no self-service path. Clears both ``submitted_at``
    and the ``auto_submitted`` flag (so the per-participant un-submit is
    decoupled from M4.2's bulk reverse-transition behaviour). No-op if the
    participant is not currently submitted. No confirmation email — admin
    follows up manually.
    """
    if participant.submitted_at is None:
        return participant

    participant.submitted_at = None
    participant.auto_submitted = False
    participant.save(update_fields=["submitted_at", "auto_submitted"])

    audit(
        action="log.release",
        actor=actor,
        target=participant.callsign,
        contest=participant.contest,
        payload={"on_behalf": True},
    )
    return participant

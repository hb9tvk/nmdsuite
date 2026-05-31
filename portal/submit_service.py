"""Finalise-and-lock action for a participant's log (M2.5).

Submission is mostly permissive: the operator decides what to file even
when individual rows have issues. But two classes of problem block
submission outright — the log being empty, and the station data missing
the minimum required equipment. Everything else surfaces as a warning
on the confirm page; the operator can still submit if they choose.

Once submitted, every editing surface in the portal becomes read-only
(the views consult ``participant.submitted_at``).

For the operator this is a one-way action; if they need to amend a
submitted log, an admin override is required. The M4 admin module supplies
that override via :func:`release_log`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.audit import audit
from core.models import Participant, QsoEntry

from .emails import send_log_submitted_confirmation
from .qso_service import detect_potential_dupe_ids
from .station_service import COMPONENT_LABELS

# Required component slots — same indexes used by the M4A.2 station-data
# table (Transceiver, Power supply, Antenna). Keeping the constants here
# avoids cross-module reach for callers that only need the validator.
_TRX_IDX = 1
_PSU_IDX = 2
_ANTENNA_IDX = 5
_REQUIRED_SLOTS = (_TRX_IDX, _PSU_IDX, _ANTENNA_IDX)


@dataclass(frozen=True)
class SubmissionValidation:
    """Result of :func:`validate_for_submission`.

    Blocking errors prevent submission entirely; warnings let the operator
    proceed if they explicitly confirm.
    """

    blocking_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def can_submit(self) -> bool:
        return not self.blocking_errors


def validate_for_submission(participant: Participant) -> SubmissionValidation:
    """Check the participant's log + station data for submission readiness."""
    blocking: list[str] = []
    warnings: list[str] = []

    qsos = list(QsoEntry.objects.filter(participant=participant))

    # --- blocking: at least one QSO ----------------------------------------------------------
    if not qsos:
        blocking.append(
            str(_("Your log is empty — submit at least one QSO before finalising.")),
        )

    # --- blocking: minimum station data -------------------------------------------------------
    if not (participant.watt or "").strip():
        blocking.append(str(_("Output power (Watt) is required in the station data.")))
    if participant.total_weight_g <= 0:
        blocking.append(str(_("Total station weight must be greater than 0 g.")))

    components_by_idx = {
        c.idx: (c.description or "").strip()
        for c in participant.components.all()
    }
    for slot in _REQUIRED_SLOTS:
        if not components_by_idx.get(slot):
            label = COMPONENT_LABELS[slot - 1]
            blocking.append(
                str(_("Station data is missing required component: %(label)s.")) % {"label": label},
            )

    # --- warnings: per-QSO validation issues -------------------------------------------------
    invalid_count = sum(1 for q in qsos if not q.is_fully_valid)
    if invalid_count:
        warnings.append(
            str(_("%(n)d QSO row(s) have invalid fields (UTC, RST, text length, etc.).")) % {"n": invalid_count},
        )

    dupe_ids = detect_potential_dupe_ids(participant) if qsos else set()
    if dupe_ids:
        warnings.append(
            str(_("%(n)d QSO(s) look like duplicates and will be deducted at scoring.")) % {"n": len(dupe_ids)},
        )

    # --- warning: weight over 6 kg ------------------------------------------------------------
    if participant.total_weight_g > 6000:
        warnings.append(str(_("Total station weight exceeds the 6 kg contest limit.")))

    return SubmissionValidation(blocking_errors=blocking, warnings=warnings)


class SubmissionRejected(Exception):
    """Raised when :func:`submit_log` refuses because validation fails.

    Carries the list of blocking-error messages so callers can surface them.
    """

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def submit_log(
    *,
    participant: Participant,
    actor: Any = None,
    rules_confirmed: bool = False,
) -> Participant:
    """Mark the participant's log as submitted. No-op if already submitted.

    Enforces blocking validation server-side as a defensive backstop —
    the confirm page already gates the button, but the service should
    refuse too in case a stale form sneaks through.

    ``actor`` defaults to the participant's own user (portal self-submit).
    Admin on-behalf submits pass in the staff user; the audit row records
    them with ``on_behalf=True`` and the confirmation email is skipped (the
    operator did not trigger this action; admin can follow up manually).

    ``rules_confirmed`` must be True for self-submits — the operator has
    to tick the contest-rules confirmation checkbox on the confirm page.
    Admin on-behalf submits bypass this check (the operator hasn't
    necessarily seen the page; staff are the final authority).

    The DB writes (flip ``submitted_at``, audit row) run inside one
    ``transaction.atomic`` block. The confirmation email is sent
    *after* the transaction commits — SMTP latency would otherwise
    keep SQLite's write lock held across a network round-trip and
    starve concurrent workers.
    """
    if participant.submitted_at is not None:
        return participant

    is_on_behalf = actor is not None and actor != participant.user

    with transaction.atomic():
        # Admin on-behalf bypasses validation — staff are the final authority.
        if not is_on_behalf:
            validation = validate_for_submission(participant)
            if not validation.can_submit:
                raise SubmissionRejected(validation.blocking_errors)
            if not rules_confirmed:
                raise SubmissionRejected([
                    str(_("Please confirm the contest-rules statement before submitting.")),
                ])

        participant.submitted_at = timezone.now()
        participant.save(update_fields=["submitted_at"])

        audit_payload: dict[str, Any] = {
            "qso_count": participant.qsos.count(),
            "total_weight_g": participant.total_weight_g,
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

    # Email is best-effort and lives outside the transaction. EmailLog
    # already records SENT / FAILED on its own row, so an SMTP problem
    # here doesn't roll back the submit.
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

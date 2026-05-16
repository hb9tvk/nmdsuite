"""Bulk-email service for the admin module (M4.4).

Sends a manual message from staff to every active participant of the
active contest. Subject and body are plain text with two supported
per-recipient placeholders, ``{callsign}`` and ``{first_name}``;
anything else in braces is left alone (so accidental ``{foo}`` does not
explode the send).

Each delivery attempt persists an :class:`core.models.EmailLog` row.
The whole bulk send writes a single ``bulk_email.send`` audit row with
counts in the payload — useful for "who sent what when" without
exploding the audit log into N rows per blast.

Synchronous on purpose: NMD participant counts are O(100), email send
is O(seconds) at SMTP backends — Celery would be overkill. If this
gets slow enough to matter, the right move is a queue, not a thread.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from django.core.mail import EmailMessage
from django.db import transaction

from core.audit import audit
from core.models import Contest, EmailLog, Participant

log = logging.getLogger("nmdsuite.email")


@dataclass
class BulkEmailResult:
    total: int = 0
    sent: int = 0
    failed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)  # (recipient, error)


def _personalise(text: str, participant: Participant) -> str:
    """Replace the supported placeholders. Unknown ``{name}`` tokens are
    left as-is so a typo in the staff-authored message doesn't crash
    the send."""
    return (
        text
        .replace("{callsign}", participant.callsign)
        .replace("{first_name}", participant.first_name)
    )


def active_recipients(contest: Contest):
    """Active (non-cancelled) participants of ``contest`` with a non-blank email."""
    return (
        Participant.objects
        .filter(contest=contest, cancelled_at__isnull=True)
        .exclude(email="")
        .order_by("callsign")
    )


def send_bulk_email(
    *, contest: Contest, subject: str, body: str, actor: Any,
) -> BulkEmailResult:
    """Send ``subject`` / ``body`` to every active participant of ``contest``.

    Returns a :class:`BulkEmailResult` summarising successes and failures.
    A failed delivery does NOT abort the run — the remaining recipients
    are still attempted. Per-recipient ``EmailLog`` rows are persisted
    individually so a crash mid-run leaves an accurate partial record.
    """
    result = BulkEmailResult()

    for participant in active_recipients(contest):
        result.total += 1
        per_subject = _personalise(subject, participant)
        per_body = _personalise(body, participant)

        log_row = EmailLog.objects.create(
            recipient=participant.email,
            subject=per_subject,
            contest=contest,
            status=EmailLog.Status.QUEUED,
        )
        try:
            EmailMessage(
                subject=per_subject, body=per_body, to=[participant.email],
            ).send(fail_silently=False)
            log_row.status = EmailLog.Status.SENT
            log_row.save(update_fields=["status"])
            result.sent += 1
        except Exception as exc:
            log.exception("Bulk email failed for %s", participant.email)
            log_row.status = EmailLog.Status.FAILED
            log_row.error = str(exc)[:500]
            log_row.save(update_fields=["status", "error"])
            result.failed += 1
            result.failures.append((participant.email, str(exc)[:200]))

    with transaction.atomic():
        audit(
            action="bulk_email.send",
            actor=actor,
            target=f"NMD {contest.year}",
            contest=contest,
            payload={
                "total": result.total,
                "sent": result.sent,
                "failed": result.failed,
                "subject": subject,
            },
        )
    return result

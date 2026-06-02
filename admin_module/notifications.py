"""State-change participant notifications (F2).

Triggered from the contest-lifecycle service when staff advance the
state machine, these broadcasts notify every active participant of the
new phase: registration closed, results published, …

Each broadcast renders the same trilingual (DE/FR/IT) template per
recipient, persists one ``EmailLog`` row per send, and writes a single
audit row at the end with success / failure counts — same pattern as
:mod:`admin_module.email_service`. Failures don't abort the run.

The actual send happens through the configured EMAIL_BACKEND
(:class:`nmdsuite.email_backends.RedirectingSMTPEmailBackend` in
practice), so dev deployments with ``EMAIL_REDIRECT_TO`` set funnel
every notification to the sink mailbox instead of real participants.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.template.loader import render_to_string

from core.audit import audit
from core.models import Contest, EmailLog, Participant

log = logging.getLogger("nmdsuite.email")


@dataclass
class BroadcastResult:
    total: int = 0
    sent: int = 0
    failed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


def _active_recipients(contest: Contest):
    return (
        Participant.objects
        .filter(contest=contest, cancelled_at__isnull=True)
        .exclude(email="")
        .order_by("callsign")
    )


def _broadcast(
    *,
    contest: Contest,
    template_base: str,
    audit_action: str,
    actor: Any,
    extra_context: dict | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> BroadcastResult:
    """Render and send ``templates/registration/email/<template_base>.txt``
    (plus the matching ``_subject.txt``) to every active participant.

    The body template gets ``participant``, ``contest``, ``portal_url``
    and anything in ``extra_context``.

    ``attachments`` is a list of ``(filename, content_bytes, mimetype)``
    tuples shared across every recipient — build the payload once and
    let the SMTP backend stream the same bytes to each address.
    """
    result = BroadcastResult()
    portal_url = f"{settings.NMD_BASE_URL.rstrip('/')}/submission/"
    common_context: dict[str, Any] = {
        "contest": contest,
        "portal_url": portal_url,
    }
    if extra_context:
        common_context.update(extra_context)

    for participant in _active_recipients(contest):
        result.total += 1
        ctx = {**common_context, "participant": participant}
        subject = render_to_string(
            f"registration/email/{template_base}_subject.txt", ctx,
        ).strip()
        body = render_to_string(f"registration/email/{template_base}.txt", ctx)

        log_row = EmailLog.objects.create(
            recipient=participant.email,
            subject=subject,
            contest=contest,
            status=EmailLog.Status.QUEUED,
        )
        try:
            message = EmailMessage(
                subject=subject, body=body, to=[participant.email],
            )
            for filename, content, mimetype in (attachments or []):
                message.attach(filename, content, mimetype)
            message.send(fail_silently=False)
            log_row.status = EmailLog.Status.SENT
            log_row.save(update_fields=["status"])
            result.sent += 1
        except Exception as exc:
            log.exception("Notification %r failed for %s", template_base, participant.email)
            log_row.status = EmailLog.Status.FAILED
            log_row.error = str(exc)[:500]
            log_row.save(update_fields=["status", "error"])
            result.failed += 1
            result.failures.append((participant.email, str(exc)[:200]))

    with transaction.atomic():
        audit(
            action=audit_action,
            actor=actor,
            target=f"NMD {contest.year}",
            contest=contest,
            payload={
                "total": result.total,
                "sent": result.sent,
                "failed": result.failed,
            },
        )
    return result


def send_registration_closed_broadcast(
    *, contest: Contest, actor: Any,
) -> BroadcastResult:
    """Notify every active participant that registration has closed.
    The current participant-list PDF is attached so recipients don't
    need to log in just to grab it. Fired from
    :func:`admin_module.services.close_registration`."""
    # Local import — pulling reportlab into every admin import path is
    # wasteful, and this is the only caller right now.
    from core.participant_list_pdf import build_participant_list_pdf

    pdf_bytes = build_participant_list_pdf(contest)
    pdf_name = f"nmd-{contest.year}-participants.pdf"
    return _broadcast(
        contest=contest,
        template_base="registration_closed",
        audit_action="contest.notify_registration_closed",
        actor=actor,
        attachments=[(pdf_name, pdf_bytes, "application/pdf")],
    )


def send_results_published_broadcast(
    *, contest: Contest, actor: Any,
) -> BroadcastResult:
    """Notify every active participant that the contest results are
    public. The body links the year-indexed public ranking page and
    each participant's own portal scoring page. Fired from
    :func:`admin_module.services.publish_results`."""
    base = settings.NMD_BASE_URL.rstrip("/")
    return _broadcast(
        contest=contest,
        template_base="results_published",
        audit_action="contest.notify_results_published",
        actor=actor,
        extra_context={
            "public_ranking_url": f"{base}/ranking/{contest.year}/",
            "portal_scoring_url": f"{base}/submission/scoring/",
        },
    )

"""Render and send the registration confirmation email."""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from core.models import Contest, EmailLog, Participant

log = logging.getLogger("nmdsuite.email")


def send_registration_confirmation(
    *,
    participant: Participant,
    contest: Contest,
    generated_password: str | None,
) -> EmailLog:
    """Render the DE/FR/IT confirmation template and send it.

    ``generated_password`` is None for returning participants; in that case the
    template tells them to use their existing password (or reset it).
    """
    portal_url = f"{settings.NMD_BASE_URL.rstrip('/')}/submission/"
    reset_url = f"{settings.NMD_BASE_URL.rstrip('/')}/submission/password-reset/"

    context = {
        "participant": participant,
        "contest": contest,
        "generated_password": generated_password,
        "portal_url": portal_url,
        "reset_url": reset_url,
    }

    subject = render_to_string("registration/email/confirmation_subject.txt", context).strip()
    body = render_to_string("registration/email/confirmation.txt", context)

    log_row = EmailLog.objects.create(
        recipient=participant.email,
        subject=subject,
        contest=contest,
        status=EmailLog.Status.QUEUED,
    )

    try:
        message = EmailMessage(
            subject=subject,
            body=body,
            to=[participant.email],
        )
        message.send(fail_silently=False)
        log_row.status = EmailLog.Status.SENT
        log_row.save(update_fields=["status"])
    except Exception as exc:
        log.exception("Failed to send registration email to %s", participant.email)
        log_row.status = EmailLog.Status.FAILED
        log_row.error = str(exc)[:500]
        log_row.save(update_fields=["status", "error"])

    return log_row

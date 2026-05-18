"""Confirmation email sent when a participant submits their log."""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from core.models import EmailLog, Participant

log = logging.getLogger("nmdsuite.email")


def send_log_submitted_confirmation(*, participant: Participant) -> EmailLog:
    """Render the trilingual log-submitted confirmation and send it."""
    portal_url = f"{settings.NMD_BASE_URL.rstrip('/')}/submission/"
    adif_url = f"{portal_url}log.adi"

    station = getattr(participant, "station", None)
    context = {
        "participant": participant,
        "contest": participant.contest,
        "portal_url": portal_url,
        "adif_url": adif_url,
        "qso_count": participant.qsos.count(),
        "total_weight_g": station.total_weight_g if station is not None else 0,
        "submitted_at": participant.submitted_at,
    }

    subject = render_to_string("portal/email/log_submitted_subject.txt", context).strip()
    body = render_to_string("portal/email/log_submitted.txt", context)

    log_row = EmailLog.objects.create(
        recipient=participant.email,
        subject=subject,
        contest=participant.contest,
        status=EmailLog.Status.QUEUED,
    )

    try:
        EmailMessage(subject=subject, body=body, to=[participant.email]).send(fail_silently=False)
        log_row.status = EmailLog.Status.SENT
        log_row.save(update_fields=["status"])
    except Exception as exc:
        log.exception("Failed to send log-submitted email to %s", participant.email)
        log_row.status = EmailLog.Status.FAILED
        log_row.error = str(exc)[:500]
        log_row.save(update_fields=["status", "error"])

    return log_row

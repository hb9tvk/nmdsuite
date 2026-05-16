"""Administration module — M4.4 bulk email."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone

from admin_module import email_service
from core.models import AuditLog, EmailLog, Participant

User = get_user_model()


def _make_staff_user(username: str = "STAFF") -> User:
    return User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org", is_staff=True,
    )


def _make_participant(
    contest, *, username, callsign, first_name=None, email=None, cancelled=False,
) -> Participant:
    user = User.objects.create_user(
        username=username, password="x", email=email or f"{username.lower()}@x.org",
    )
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign,
        first_name=first_name or username,
        email=email or f"{username.lower()}@x.org",
        coord_system_input="wgs84", coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2, ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", operating_modes=3,
        cancelled_at=timezone.now() if cancelled else None,
    )


# --- service: active_recipients --------------------------------------------------------------


@pytest.mark.django_db
def test_active_recipients_excludes_cancelled(seeded_contest):
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A")
    _make_participant(seeded_contest, username="HB9B", callsign="HB9B", cancelled=True)
    recipients = list(email_service.active_recipients(seeded_contest))
    callsigns = [p.callsign for p in recipients]
    assert callsigns == ["HB9A"]


@pytest.mark.django_db
def test_active_recipients_excludes_blank_email(seeded_contest):
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A")
    p = _make_participant(seeded_contest, username="HB9B", callsign="HB9B")
    p.email = ""
    p.save(update_fields=["email"])
    recipients = list(email_service.active_recipients(seeded_contest))
    assert [r.callsign for r in recipients] == ["HB9A"]


# --- service: send_bulk_email ----------------------------------------------------------------


@pytest.mark.django_db
def test_send_bulk_email_delivers_to_all_active(seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A", email="a@x.org")
    _make_participant(seeded_contest, username="HB9B", callsign="HB9B", email="b@x.org")
    _make_participant(seeded_contest, username="HB9C", callsign="HB9C", email="c@x.org", cancelled=True)
    staff = _make_staff_user()

    result = email_service.send_bulk_email(
        contest=seeded_contest, subject="Hi", body="Body", actor=staff,
    )

    assert result.total == 2
    assert result.sent == 2
    assert result.failed == 0
    # Two outbox messages, addressed to the two active participants.
    addressed = sorted(msg.to[0] for msg in mail.outbox)
    assert addressed == ["a@x.org", "b@x.org"]
    # Per-recipient EmailLog rows with SENT status.
    assert EmailLog.objects.filter(status=EmailLog.Status.SENT).count() == 2


@pytest.mark.django_db
def test_send_bulk_email_substitutes_placeholders(seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _make_participant(
        seeded_contest, username="HB9A", callsign="HB9A/P", first_name="Anna", email="a@x.org",
    )
    staff = _make_staff_user()

    email_service.send_bulk_email(
        contest=seeded_contest,
        subject="Hi {first_name}",
        body="Your callsign is {callsign}. Thanks {first_name}!",
        actor=staff,
    )

    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.subject == "Hi Anna"
    assert msg.body == "Your callsign is HB9A/P. Thanks Anna!"


@pytest.mark.django_db
def test_send_bulk_email_leaves_unknown_placeholders_alone(seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A", email="a@x.org")
    staff = _make_staff_user()

    email_service.send_bulk_email(
        contest=seeded_contest,
        subject="Hi {callsign}",
        body="Unknown: {foo} should stay as-is.",
        actor=staff,
    )

    msg = mail.outbox[0]
    assert "{foo}" in msg.body


@pytest.mark.django_db
def test_send_bulk_email_logs_one_audit_row_with_counts(seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A", email="a@x.org")
    _make_participant(seeded_contest, username="HB9B", callsign="HB9B", email="b@x.org")
    staff = _make_staff_user()

    email_service.send_bulk_email(
        contest=seeded_contest, subject="Hi", body="Body", actor=staff,
    )

    entries = AuditLog.objects.filter(action="bulk_email.send")
    assert entries.count() == 1
    entry = entries.first()
    assert entry.actor == staff
    assert entry.payload["total"] == 2
    assert entry.payload["sent"] == 2
    assert entry.payload["failed"] == 0
    assert entry.payload["subject"] == "Hi"


@pytest.mark.django_db
def test_send_bulk_email_continues_past_failed_recipient(seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A", email="a@x.org")
    _make_participant(seeded_contest, username="HB9B", callsign="HB9B", email="b@x.org")
    _make_participant(seeded_contest, username="HB9C", callsign="HB9C", email="c@x.org")
    staff = _make_staff_user()

    # Make the SECOND send blow up; the third must still go through.
    real_send = mail.EmailMessage.send
    state = {"calls": 0}

    def flaky_send(self, *args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 2:
            raise RuntimeError("smtp went sideways")
        return real_send(self, *args, **kwargs)

    with patch.object(mail.EmailMessage, "send", flaky_send):
        result = email_service.send_bulk_email(
            contest=seeded_contest, subject="Hi", body="Body", actor=staff,
        )

    assert result.total == 3
    assert result.sent == 2
    assert result.failed == 1
    assert len(result.failures) == 1
    failed_log = EmailLog.objects.get(status=EmailLog.Status.FAILED)
    assert "smtp went sideways" in failed_log.error
    # Audit row still written with the correct counts.
    entry = AuditLog.objects.get(action="bulk_email.send")
    assert entry.payload == {
        "total": 3, "sent": 2, "failed": 1, "subject": "Hi",
    }


# --- view: access control --------------------------------------------------------------------


@pytest.mark.django_db
def test_bulk_email_redirects_non_staff(client, seeded_contest):
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.get("/admin/email/")
    assert response.status_code in (302, 403)


# --- view: rendering -------------------------------------------------------------------------


@pytest.mark.django_db
def test_bulk_email_get_renders_form_and_recipient_count(client, seeded_contest):
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A", email="a@x.org")
    _make_participant(seeded_contest, username="HB9B", callsign="HB9B", email="b@x.org")
    client.force_login(_make_staff_user())
    response = client.get("/admin/email/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "Subject" in body
    assert "2 active participants" in body


# --- view: sending ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_bulk_email_post_confirmed_sends_and_redirects(client, seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A", email="a@x.org")
    staff = _make_staff_user()
    client.force_login(staff)

    response = client.post(
        "/admin/email/",
        {"subject": "Hi", "body": "Body", "confirmed": "1"},
    )
    assert response.status_code == 302
    assert response["Location"].endswith("/admin/email/")
    assert len(mail.outbox) == 1
    assert AuditLog.objects.filter(action="bulk_email.send", actor=staff).exists()


@pytest.mark.django_db
def test_bulk_email_post_without_confirmed_does_not_send(client, seeded_contest, settings):
    """The hidden ``confirmed=1`` is the safety guard. Without it (e.g. an
    accidental POST without the form's submit button) the view must NOT fire."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A", email="a@x.org")
    client.force_login(_make_staff_user())

    response = client.post("/admin/email/", {"subject": "Hi", "body": "Body"})
    assert response.status_code == 200
    assert mail.outbox == []
    assert not AuditLog.objects.filter(action="bulk_email.send").exists()


@pytest.mark.django_db
def test_bulk_email_form_required_fields(client, seeded_contest):
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A", email="a@x.org")
    client.force_login(_make_staff_user())
    response = client.post(
        "/admin/email/", {"subject": "", "body": "", "confirmed": "1"},
    )
    assert response.status_code == 200  # re-render with errors
    body = response.content.decode()
    assert "required" in body.lower() or "form-errors" in body

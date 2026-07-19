"""Administration module — prewritten log-submission reminder broadcast."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone

from admin_module import notifications
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


# --- service: send_log_reminder_broadcast ------------------------------------------------------


@pytest.mark.django_db
def test_log_reminder_sends_only_to_active(seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A", email="a@x.org")
    _make_participant(seeded_contest, username="HB9B", callsign="HB9B", email="b@x.org")
    _make_participant(seeded_contest, username="HB9C", callsign="HB9C", email="c@x.org", cancelled=True)
    staff = _make_staff_user()

    result = notifications.send_log_reminder_broadcast(
        contest=seeded_contest, actor=staff,
    )

    assert result.total == 2
    assert result.sent == 2
    assert sorted(msg.to[0] for msg in mail.outbox) == ["a@x.org", "b@x.org"]
    assert EmailLog.objects.filter(status=EmailLog.Status.SENT).count() == 2


@pytest.mark.django_db
def test_log_reminder_body_is_trilingual_and_personalised(seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _make_participant(
        seeded_contest, username="HB9A", callsign="HB9A", first_name="Anna", email="a@x.org",
    )
    staff = _make_staff_user()

    notifications.send_log_reminder_broadcast(contest=seeded_contest, actor=staff)

    msg = mail.outbox[0]
    body = msg.body
    # Personalised salutation in every language section.
    assert "Hallo Anna" in body
    assert "Bonjour Anna" in body
    assert "Ciao Anna" in body
    # Portal link derived from settings, not hardcoded.
    assert f"{settings.NMD_BASE_URL.rstrip('/')}/submission/" in body
    # Date-agnostic: the deadline is relative, never a calendar date.
    assert "innert 8 Tagen" in body
    assert "26.7" not in body
    # Gathering reminder present.
    assert "nmd-treffen" in body
    assert str(seeded_contest.year) in msg.subject


@pytest.mark.django_db
def test_log_reminder_writes_one_audit_row(seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A", email="a@x.org")
    staff = _make_staff_user()

    notifications.send_log_reminder_broadcast(contest=seeded_contest, actor=staff)

    entries = AuditLog.objects.filter(action="contest.notify_log_reminder")
    assert entries.count() == 1
    entry = entries.first()
    assert entry.actor == staff
    assert entry.payload == {"total": 1, "sent": 1, "failed": 0}


# --- view --------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_log_reminder_view_redirects_non_staff(client, seeded_contest):
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.get("/admin/email/log-reminder/")
    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_log_reminder_view_get_shows_recipients_and_preview(client, seeded_contest):
    _make_participant(
        seeded_contest, username="HB9A", callsign="HB9A", first_name="Anna", email="a@x.org",
    )
    client.force_login(_make_staff_user())

    response = client.get("/admin/email/log-reminder/")

    assert response.status_code == 200
    content = response.content.decode()
    assert "HB9A" in content
    assert "Hallo Anna" in content       # preview personalised with first recipient
    assert "=== Français ===" in content
    assert len(mail.outbox) == 0         # GET must not send anything


@pytest.mark.django_db
def test_log_reminder_view_post_sends_and_redirects(client, seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A", email="a@x.org")
    _make_participant(seeded_contest, username="HB9B", callsign="HB9B", email="b@x.org", cancelled=True)
    client.force_login(_make_staff_user())

    response = client.post("/admin/email/log-reminder/")

    assert response.status_code == 302
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["a@x.org"]

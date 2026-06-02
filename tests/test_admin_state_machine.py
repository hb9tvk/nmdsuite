"""Contest lifecycle transitions (M4.2)."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from admin_module import services
from admin_module.services import TransitionError
from core.models import AuditLog, Contest, Participant

User = get_user_model()


def _make_staff_user():
    return User.objects.create_user(
        username="STAFF", password="x", email="staff@x.org", is_staff=True,
    )


def _make_participant(contest, *, username, callsign, submitted=False, cancelled=False):
    user = User.objects.create_user(username=username, password="x", email=f"{username.lower()}@x.org")
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign, first_name=username,
        email=f"{username.lower()}@x.org", coord_system_input="wgs84",
        coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2, ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", operating_modes=3,
        submitted_at=timezone.now() if submitted else None,
        cancelled_at=timezone.now() if cancelled else None,
    )


# --- close_registration ----------------------------------------------------------------------


@pytest.mark.django_db
def test_close_registration_happy_path(seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    staff = _make_staff_user()
    assert seeded_contest.state == Contest.State.REGISTRATION_OPEN
    services.close_registration(seeded_contest, actor=staff)
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.REGISTRATION_CLOSED
    assert AuditLog.objects.filter(action="contest.close_registration").exists()


@pytest.mark.django_db
def test_close_registration_rejects_wrong_state(seeded_contest):
    seeded_contest.state = Contest.State.LOGS_CLOSED
    seeded_contest.save()
    with pytest.raises(TransitionError):
        services.close_registration(seeded_contest, actor=_make_staff_user())


@pytest.mark.django_db
def test_close_registration_notifies_active_participants(seeded_contest, settings):
    """Each active participant receives a registration-closed mail.
    Cancelled rows and rows with a blank email are skipped. A single
    audit row records the broadcast outcome."""
    from django.core import mail
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    active = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    other = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ/P")
    _make_participant(
        seeded_contest, username="HB9CAN", callsign="HB9CAN/P", cancelled=True,
    )

    services.close_registration(seeded_contest, actor=_make_staff_user())

    recipients = sorted(addr for msg in mail.outbox for addr in msg.to)
    assert recipients == sorted([active.email, other.email])
    # Subject + body use the right templates.
    sample = mail.outbox[0]
    assert f"NMD {seeded_contest.year}" in sample.subject
    assert "Registration closed" in sample.subject or "geschlossen" in sample.subject
    assert "=== Deutsch ===" in sample.body
    assert "=== Français ===" in sample.body
    assert "=== Italiano ===" in sample.body
    # Participant-list PDF is attached to every message.
    for msg in mail.outbox:
        assert len(msg.attachments) == 1
        name, content, mimetype = msg.attachments[0]
        assert name == f"nmd-{seeded_contest.year}-participants.pdf"
        assert mimetype == "application/pdf"
        assert content.startswith(b"%PDF-")
    # Broadcast audited with counts.
    entry = AuditLog.objects.get(action="contest.notify_registration_closed")
    assert entry.payload["sent"] == 2
    assert entry.payload["failed"] == 0


@pytest.mark.django_db
def test_close_registration_broadcast_failure_does_not_block(seeded_contest, settings):
    """If SMTP refuses one recipient, the others still get through and
    the audit row reflects the partial outcome."""
    from django.core import mail
    from unittest.mock import patch

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    _make_participant(seeded_contest, username="HB9OK", callsign="HB9OK/P")
    _make_participant(seeded_contest, username="HB9BAD", callsign="HB9BAD/P")

    original_send = mail.EmailMessage.send

    def _flaky_send(self, *args, **kwargs):
        if self.to == ["hb9bad@x.org"]:
            raise RuntimeError("simulated SMTP refusal")
        return original_send(self, *args, **kwargs)

    with patch("django.core.mail.EmailMessage.send", _flaky_send):
        services.close_registration(seeded_contest, actor=_make_staff_user())

    entry = AuditLog.objects.get(action="contest.notify_registration_closed")
    assert entry.payload == {"total": 2, "sent": 1, "failed": 1}


# --- close_log_submission --------------------------------------------------------------------


@pytest.mark.django_db
def test_close_log_submission_auto_submits_pending(seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save()
    submitted = _make_participant(seeded_contest, username="A1", callsign="A1/P", submitted=True)
    pending1 = _make_participant(seeded_contest, username="A2", callsign="A2/P")
    pending2 = _make_participant(seeded_contest, username="A3", callsign="A3/P")
    cancelled = _make_participant(seeded_contest, username="A4", callsign="A4/P", cancelled=True)
    original_submitted_at = submitted.submitted_at

    n = services.close_log_submission(seeded_contest, actor=_make_staff_user())
    assert n == 2  # only the two pending ones got auto-submitted

    submitted.refresh_from_db()
    pending1.refresh_from_db()
    pending2.refresh_from_db()
    cancelled.refresh_from_db()

    # Already-submitted: untouched.
    assert submitted.submitted_at == original_submitted_at
    # Pending: now submitted.
    assert pending1.submitted_at is not None
    assert pending2.submitted_at is not None
    # Cancelled: still no submission (they opted out).
    assert cancelled.submitted_at is None

    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.LOGS_CLOSED
    audit = AuditLog.objects.filter(action="contest.close_logs").first()
    assert audit is not None
    assert audit.payload == {"auto_submitted": 2}


@pytest.mark.django_db
def test_close_log_submission_rejects_wrong_state(seeded_contest):
    with pytest.raises(TransitionError):
        services.close_log_submission(seeded_contest, actor=_make_staff_user())


@pytest.mark.django_db
def test_close_log_submission_emails_auto_submitted_participants(seeded_contest, settings):
    """Each pending participant the close auto-submits gets the same
    log-submitted confirmation a self-submit would have triggered.
    Already-submitted and cancelled participants do NOT get a fresh
    confirmation here."""
    from django.core import mail
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save()
    _make_participant(seeded_contest, username="A1", callsign="A1/P", submitted=True)
    pending = _make_participant(seeded_contest, username="A2", callsign="A2/P")
    _make_participant(seeded_contest, username="A3", callsign="A3/P", cancelled=True)

    services.close_log_submission(seeded_contest, actor=_make_staff_user())

    # Only the auto-submitted participant should have received a mail.
    recipients = sorted(addr for msg in mail.outbox for addr in msg.to)
    assert recipients == [pending.email]
    msg = mail.outbox[0]
    assert pending.callsign in msg.subject
    # Body uses the standard log-submitted template (trilingual sections,
    # contains the ADIF download link).
    assert "=== Deutsch ===" in msg.body
    assert "/submission/log.adi" in msg.body


# --- publish_results -------------------------------------------------------------------------


@pytest.mark.django_db
def test_publish_results_happy_path(seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    seeded_contest.state = Contest.State.LOGS_CLOSED
    seeded_contest.save()
    services.publish_results(seeded_contest, actor=_make_staff_user())
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.PUBLISHED
    assert seeded_contest.results_published_at is not None


@pytest.mark.django_db
def test_publish_results_rejects_wrong_state(seeded_contest):
    # Still REGISTRATION_OPEN — can't publish yet.
    with pytest.raises(TransitionError):
        services.publish_results(seeded_contest, actor=_make_staff_user())


@pytest.mark.django_db
def test_publish_results_notifies_active_participants(seeded_contest, settings):
    """Each active participant gets a results-published mail with links
    to the public ranking page and their personal portal scoring page."""
    from django.core import mail
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    a = _make_participant(seeded_contest, username="HB9TVK", callsign="HB9TVK/P")
    b = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ/P")
    _make_participant(
        seeded_contest, username="HB9CAN", callsign="HB9CAN/P", cancelled=True,
    )
    seeded_contest.state = Contest.State.LOGS_CLOSED
    seeded_contest.save()

    services.publish_results(seeded_contest, actor=_make_staff_user())

    recipients = sorted(addr for msg in mail.outbox for addr in msg.to)
    assert recipients == sorted([a.email, b.email])
    sample = mail.outbox[0]
    assert f"NMD {seeded_contest.year}" in sample.subject
    assert (
        "Results published" in sample.subject
        or "veröffentlicht" in sample.subject
    )
    assert "=== Deutsch ===" in sample.body
    assert "=== Français ===" in sample.body
    assert "=== Italiano ===" in sample.body
    # Both navigation links surface in the body.
    assert f"/ranking/{seeded_contest.year}/" in sample.body
    assert "/submission/scoring/" in sample.body
    # No attachments on this one (unlike F2a).
    assert sample.attachments == []
    entry = AuditLog.objects.get(action="contest.notify_results_published")
    assert entry.payload["sent"] == 2
    assert entry.payload["failed"] == 0


# --- setup_new_contest -----------------------------------------------------------------------


@pytest.mark.django_db
def test_setup_new_contest_archives_and_deactivates(seeded_contest):
    staff = _make_staff_user()
    p1 = _make_participant(seeded_contest, username="A1", callsign="A1/P")
    # Superuser should NOT be deactivated.
    super_user = User.objects.create_superuser(username="ADMIN", password="x", email="admin@x.org")

    new_contest = services.setup_new_contest(year=2027, actor=staff)

    # Old contest archived; new one is the active one now.
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.ARCHIVED
    assert new_contest.year == 2027
    assert new_contest.state == Contest.State.REGISTRATION_OPEN

    # Non-staff non-super participant: deactivated.
    p1.user.refresh_from_db()
    assert p1.user.is_active is False
    # Staff and super: untouched.
    staff.refresh_from_db()
    assert staff.is_active is True
    super_user.refresh_from_db()
    assert super_user.is_active is True

    audit = AuditLog.objects.filter(action="contest.setup_new").first()
    assert audit is not None
    assert audit.payload["archived_contests"] >= 1
    assert audit.payload["deactivated_users"] == 1  # just p1


@pytest.mark.django_db
def test_setup_new_contest_rejects_existing_year(seeded_contest):
    with pytest.raises(TransitionError, match="already exists"):
        services.setup_new_contest(year=seeded_contest.year, actor=_make_staff_user())


@pytest.mark.django_db
def test_setup_new_contest_rejects_year_out_of_range(seeded_contest):
    with pytest.raises(TransitionError, match="out of supported range"):
        services.setup_new_contest(year=1900, actor=_make_staff_user())


# --- reverse transitions ---------------------------------------------------------------------


@pytest.mark.django_db
def test_revert_close_registration(seeded_contest):
    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save()
    services.revert_close_registration(seeded_contest, actor=_make_staff_user())
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.REGISTRATION_OPEN
    assert AuditLog.objects.filter(action="contest.revert_close_registration").exists()


@pytest.mark.django_db
def test_revert_close_log_submission_undoes_only_auto_submits(seeded_contest):
    """Closing + reverting logs leaves operator-submitted rows alone but
    un-locks the rows that the close action auto-submitted."""
    staff = _make_staff_user()
    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save()
    operator_submitted = _make_participant(seeded_contest, username="OPS", callsign="OPS/P", submitted=True)
    pending = _make_participant(seeded_contest, username="PND", callsign="PND/P")
    original_submitted_at = operator_submitted.submitted_at

    services.close_log_submission(seeded_contest, actor=staff)
    pending.refresh_from_db()
    assert pending.submitted_at is not None
    assert pending.auto_submitted is True

    n = services.revert_close_log_submission(seeded_contest, actor=staff)
    seeded_contest.refresh_from_db()
    operator_submitted.refresh_from_db()
    pending.refresh_from_db()

    assert n == 1
    assert seeded_contest.state == Contest.State.REGISTRATION_CLOSED
    # Operator's own submission untouched.
    assert operator_submitted.submitted_at == original_submitted_at
    assert operator_submitted.auto_submitted is False
    # Pending row: fully un-submitted, ready to edit again.
    assert pending.submitted_at is None
    assert pending.auto_submitted is False
    audit = AuditLog.objects.filter(action="contest.revert_close_logs").first()
    assert audit.payload == {"un_submitted": 1}


@pytest.mark.django_db
def test_revert_publish_results_clears_published_at(seeded_contest):
    staff = _make_staff_user()
    seeded_contest.state = Contest.State.LOGS_CLOSED
    seeded_contest.save()
    services.publish_results(seeded_contest, actor=staff)
    seeded_contest.refresh_from_db()
    assert seeded_contest.results_published_at is not None

    services.revert_publish_results(seeded_contest, actor=staff)
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.LOGS_CLOSED
    assert seeded_contest.results_published_at is None


@pytest.mark.django_db
def test_revert_rejects_wrong_state(seeded_contest):
    staff = _make_staff_user()
    # REGISTRATION_OPEN — nothing to revert from.
    with pytest.raises(TransitionError):
        services.revert_close_registration(seeded_contest, actor=staff)
    with pytest.raises(TransitionError):
        services.revert_publish_results(seeded_contest, actor=staff)


# --- single-button revert view --------------------------------------------------------------


@pytest.mark.django_db
def test_view_revert_dispatches_from_published(client, seeded_contest):
    seeded_contest.state = Contest.State.PUBLISHED
    seeded_contest.results_published_at = timezone.now()
    seeded_contest.save()
    client.force_login(_make_staff_user())
    response = client.post("/admin/contest/revert/")
    assert response.status_code == 302
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.LOGS_CLOSED
    assert seeded_contest.results_published_at is None


@pytest.mark.django_db
def test_view_revert_no_op_when_already_at_first_state(client, seeded_contest):
    """REGISTRATION_OPEN is the earliest state — revert button shouldn't be
    shown and POST should error gracefully if someone hits the URL directly."""
    client.force_login(_make_staff_user())
    response = client.post("/admin/contest/revert/")
    assert response.status_code == 302
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.REGISTRATION_OPEN


# --- view smoke tests ------------------------------------------------------------------------


@pytest.mark.django_db
def test_view_close_registration_post(client, seeded_contest):
    client.force_login(_make_staff_user())
    response = client.post("/admin/contest/close-registration/")
    assert response.status_code == 302
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.REGISTRATION_CLOSED


@pytest.mark.django_db
def test_view_close_log_submission_post_auto_submits(client, seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save()
    pending = _make_participant(seeded_contest, username="P1", callsign="P1/P")
    client.force_login(_make_staff_user())
    response = client.post("/admin/contest/close-logs/")
    assert response.status_code == 302
    pending.refresh_from_db()
    assert pending.submitted_at is not None


@pytest.mark.django_db
def test_view_publish_results_post(client, seeded_contest):
    seeded_contest.state = Contest.State.LOGS_CLOSED
    seeded_contest.save()
    client.force_login(_make_staff_user())
    response = client.post("/admin/contest/publish/")
    assert response.status_code == 302
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.PUBLISHED


# --- auto-rescore on close_log_submission ----------------------------------------------------


@pytest.mark.django_db
def test_close_log_submission_auto_runs_scoring(seeded_contest, settings):
    """The close-logs transition must run the scoring pipeline as part
    of the same atomic action — the LOGS_CLOSED state always lands with
    fresh ScoringRecord rows, so the admin doesn't have a separate
    'now run scoring' step to remember."""
    from core.models import QsoEntry, ScoringRecord
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save()
    p = _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P")
    QsoEntry.objects.create(
        participant=p, utc_raw="0700", utc_time=seeded_contest.start_utc,
        mode="CW", remote_call="DL1ABC", rsts="599", rstr="599",
    )

    services.close_log_submission(seeded_contest, actor=_make_staff_user())

    # ScoringRecord was created.
    assert ScoringRecord.objects.filter(qso__participant=p).exists()
    # Both audit rows present, distinguishable by action.
    assert AuditLog.objects.filter(action="contest.close_logs").exists()
    scoring = AuditLog.objects.get(action="scoring.run")
    assert scoring.payload["source"] == "close_logs"


# --- manual rescore button ------------------------------------------------------------------


@pytest.mark.django_db
def test_rescore_button_runs_scoring_with_manual_source(client, seeded_contest):
    from core.models import QsoEntry, ScoringRecord

    seeded_contest.state = Contest.State.LOGS_CLOSED
    seeded_contest.save()
    p = _make_participant(seeded_contest, username="HB9A", callsign="HB9A/P", submitted=True)
    QsoEntry.objects.create(
        participant=p, utc_raw="0700", utc_time=seeded_contest.start_utc,
        mode="CW", remote_call="DL1ABC", rsts="599", rstr="599",
    )
    client.force_login(_make_staff_user())

    response = client.post("/admin/contest/rescore/")
    assert response.status_code == 302
    assert ScoringRecord.objects.filter(qso__participant=p).exists()
    scoring = AuditLog.objects.get(action="scoring.run")
    assert scoring.payload["source"] == "manual"


@pytest.mark.django_db
def test_rescore_button_rejected_before_logs_close(client, seeded_contest):
    """Re-run scoring is meaningless while operators are still logging —
    block the button in REGISTRATION_OPEN / REGISTRATION_CLOSED states."""
    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save()
    client.force_login(_make_staff_user())

    response = client.post("/admin/contest/rescore/")
    assert response.status_code == 302  # redirect with error flash
    assert not AuditLog.objects.filter(action="scoring.run").exists()


@pytest.mark.django_db
def test_rescore_get_returns_405(client, seeded_contest):
    """POST-only, like the other lifecycle endpoints."""
    seeded_contest.state = Contest.State.LOGS_CLOSED
    seeded_contest.save()
    client.force_login(_make_staff_user())
    assert client.get("/admin/contest/rescore/").status_code == 405


@pytest.mark.django_db
def test_view_transitions_get_returns_405(client, seeded_contest):
    """All transitions are POST-only — guards against accidental GET-triggers (e.g. crawlers)."""
    client.force_login(_make_staff_user())
    for path in (
        "/admin/contest/close-registration/",
        "/admin/contest/close-logs/",
        "/admin/contest/publish/",
        "/admin/contest/setup-new/",
    ):
        response = client.get(path)
        assert response.status_code == 405, f"GET {path} should be 405"


@pytest.mark.django_db
def test_view_setup_new_contest_post(client, seeded_contest):
    client.force_login(_make_staff_user())
    response = client.post("/admin/contest/setup-new/", {"year": "2028"})
    assert response.status_code == 302
    assert Contest.objects.filter(year=2028).exists()
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.ARCHIVED


@pytest.mark.django_db
def test_view_setup_new_contest_post_invalid_year(client, seeded_contest):
    client.force_login(_make_staff_user())
    response = client.post("/admin/contest/setup-new/", {"year": "abc"})
    assert response.status_code == 302
    # Original contest unchanged.
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.REGISTRATION_OPEN


@pytest.mark.django_db
def test_view_transitions_require_staff(client, seeded_contest):
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.post("/admin/contest/close-registration/")
    assert response.status_code in (302, 403)
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.REGISTRATION_OPEN

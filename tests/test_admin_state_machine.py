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
def test_close_registration_happy_path(seeded_contest):
    staff = _make_staff_user()
    assert seeded_contest.state == Contest.State.REGISTRATION_OPEN
    services.close_registration(seeded_contest, actor=staff)
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.REGISTRATION_CLOSED
    assert AuditLog.objects.filter(action="contest.close_registration").exists()


@pytest.mark.django_db
def test_close_registration_rejects_wrong_state(seeded_contest):
    seeded_contest.state = Contest.State.LOGS_OPEN
    seeded_contest.save()
    with pytest.raises(TransitionError):
        services.close_registration(seeded_contest, actor=_make_staff_user())


# --- open_log_submission ---------------------------------------------------------------------


@pytest.mark.django_db
def test_open_log_submission_happy_path(seeded_contest):
    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save()
    services.open_log_submission(seeded_contest, actor=_make_staff_user())
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.LOGS_OPEN
    assert AuditLog.objects.filter(action="contest.open_logs").exists()


@pytest.mark.django_db
def test_open_log_submission_rejects_wrong_state(seeded_contest):
    # Still REGISTRATION_OPEN — can't skip ahead.
    with pytest.raises(TransitionError):
        services.open_log_submission(seeded_contest, actor=_make_staff_user())


# --- close_log_submission --------------------------------------------------------------------


@pytest.mark.django_db
def test_close_log_submission_auto_submits_pending(seeded_contest):
    seeded_contest.state = Contest.State.LOGS_OPEN
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


# --- publish_results -------------------------------------------------------------------------


@pytest.mark.django_db
def test_publish_results_happy_path(seeded_contest):
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


# --- view smoke tests ------------------------------------------------------------------------


@pytest.mark.django_db
def test_view_close_registration_post(client, seeded_contest):
    client.force_login(_make_staff_user())
    response = client.post("/admin/contest/close-registration/")
    assert response.status_code == 302
    seeded_contest.refresh_from_db()
    assert seeded_contest.state == Contest.State.REGISTRATION_CLOSED


@pytest.mark.django_db
def test_view_close_log_submission_post_auto_submits(client, seeded_contest):
    seeded_contest.state = Contest.State.LOGS_OPEN
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


@pytest.mark.django_db
def test_view_transitions_get_returns_405(client, seeded_contest):
    """All transitions are POST-only — guards against accidental GET-triggers (e.g. crawlers)."""
    client.force_login(_make_staff_user())
    for path in (
        "/admin/contest/close-registration/",
        "/admin/contest/open-logs/",
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

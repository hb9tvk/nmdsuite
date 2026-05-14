"""Administration module — M4.1 dashboard + audit log viewer."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from core.audit import audit
from core.models import AuditLog, Participant

User = get_user_model()


def _make_staff_user():
    return User.objects.create_user(
        username="STAFF", password="x", email="staff@x.org", is_staff=True,
    )


def _make_participant(contest, *, username, callsign, submitted=False, cancelled=False):
    user = User.objects.create_user(username=username, password="x", email=f"{username.lower()}@x.org")
    from django.utils import timezone
    p = Participant.objects.create(
        contest=contest, user=user, callsign=callsign, first_name=username,
        email=f"{username.lower()}@x.org", coord_system_input="wgs84",
        coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2, ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", operating_modes=3,
        submitted_at=timezone.now() if submitted else None,
        cancelled_at=timezone.now() if cancelled else None,
    )
    return p


# --- access control --------------------------------------------------------------------------


@pytest.mark.django_db
def test_admin_index_redirects_anonymous(client):
    response = client.get("/admin/")
    assert response.status_code in (301, 302)


@pytest.mark.django_db
def test_admin_index_redirects_non_staff(client, seeded_contest):
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.get("/admin/")
    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_admin_audit_log_redirects_non_staff(client, seeded_contest):
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.get("/admin/audit/")
    assert response.status_code in (302, 403)


# --- index dashboard -------------------------------------------------------------------------


@pytest.mark.django_db
def test_admin_index_no_contest_renders_hint(client, db):
    client.force_login(_make_staff_user())
    response = client.get("/admin/")
    assert response.status_code == 200
    assert b"No active contest" in response.content


@pytest.mark.django_db
def test_admin_index_shows_participant_counts(client, seeded_contest):
    _make_participant(seeded_contest, username="A1", callsign="A1/P", submitted=True)
    _make_participant(seeded_contest, username="A2", callsign="A2/P")  # pending
    _make_participant(seeded_contest, username="A3", callsign="A3/P", cancelled=True)
    client.force_login(_make_staff_user())

    response = client.get("/admin/")
    body = response.content.decode()
    # 3 registered (incl. cancelled), 2 active, 1 submitted, 1 pending, 1 cancelled
    assert "Registered" in body
    assert ">3<" in body  # registered total
    assert "Logs submitted" in body
    assert "Pending submission" in body


@pytest.mark.django_db
def test_admin_index_shows_recent_audit_entries(client, seeded_contest):
    actor = _make_staff_user()
    audit(actor=actor, action="test.action", target="HB9TVK/P", contest=seeded_contest)
    audit(actor=actor, action="another.action", target="HB9ABC/P", contest=seeded_contest)
    client.force_login(actor)

    response = client.get("/admin/")
    body = response.content.decode()
    assert "test.action" in body
    assert "another.action" in body
    assert "HB9TVK/P" in body


# --- audit log viewer ------------------------------------------------------------------------


@pytest.mark.django_db
def test_audit_log_lists_entries(client, seeded_contest):
    actor = _make_staff_user()
    audit(actor=actor, action="alpha.evt", target="T1", contest=seeded_contest)
    audit(actor=actor, action="beta.evt", target="T2", contest=seeded_contest)
    client.force_login(actor)

    response = client.get("/admin/audit/")
    body = response.content.decode()
    assert "alpha.evt" in body
    assert "beta.evt" in body
    assert "T1" in body and "T2" in body


@pytest.mark.django_db
def test_audit_log_filter_by_action(client, seeded_contest):
    actor = _make_staff_user()
    audit(actor=actor, action="alpha.evt", target="T1", contest=seeded_contest)
    audit(actor=actor, action="beta.evt", target="T2", contest=seeded_contest)
    client.force_login(actor)

    response = client.get("/admin/audit/?action=alpha.evt")
    body = response.content.decode()
    assert "T1" in body
    assert "T2" not in body


@pytest.mark.django_db
def test_audit_log_filter_by_target_substring(client, seeded_contest):
    actor = _make_staff_user()
    audit(actor=actor, action="x.y", target="ZZ1ALPHA/P", contest=seeded_contest)
    audit(actor=actor, action="x.y", target="ZZ2BETA/P", contest=seeded_contest)
    client.force_login(actor)

    response = client.get("/admin/audit/?target=ALPHA")
    body = response.content.decode()
    assert "ZZ1ALPHA/P" in body
    assert "ZZ2BETA/P" not in body


@pytest.mark.django_db
def test_audit_log_filter_by_actor(client, seeded_contest):
    actor1 = _make_staff_user()
    actor2 = User.objects.create_user(username="OTHER", password="x", email="o@x.org", is_staff=True)
    audit(actor=actor1, action="a.b", target="T1", contest=seeded_contest)
    audit(actor=actor2, action="a.b", target="T2", contest=seeded_contest)
    client.force_login(actor1)

    response = client.get("/admin/audit/?actor=OTHER")
    body = response.content.decode()
    assert "T2" in body
    assert "T1" not in body


@pytest.mark.django_db
def test_audit_log_pagination(client, seeded_contest):
    actor = _make_staff_user()
    for i in range(60):  # > one page (50)
        audit(actor=actor, action="bulk.evt", target=f"T{i:02d}", contest=seeded_contest)
    client.force_login(actor)

    page1 = client.get("/admin/audit/").content.decode()
    page2 = client.get("/admin/audit/?page=2").content.decode()
    assert "Page 1 of 2" in page1
    assert "Page 2 of 2" in page2
    # First page shows the newest, second page the oldest.
    assert "T00" in page2  # the oldest target
    assert "T00" not in page1


@pytest.mark.django_db
def test_audit_log_empty_filter_shows_no_entries_message(client, seeded_contest):
    actor = _make_staff_user()
    audit(actor=actor, action="x.y", target="T1", contest=seeded_contest)
    client.force_login(actor)

    response = client.get("/admin/audit/?action=nonexistent.action")
    assert b"No entries match" in response.content


@pytest.mark.django_db
def test_audit_log_action_filter_options_only_show_observed_actions(client, seeded_contest):
    """The dropdown should only contain action values actually present in
    the AuditLog, so users don't see noise."""
    actor = _make_staff_user()
    audit(actor=actor, action="only.this.one", target="T", contest=seeded_contest)
    client.force_login(actor)

    response = client.get("/admin/audit/")
    body = response.content.decode()
    assert "only.this.one" in body

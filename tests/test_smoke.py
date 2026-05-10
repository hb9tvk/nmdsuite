"""Smoke tests — confirm the project boots and the basic plumbing works."""
from __future__ import annotations

from datetime import date

import pytest


def test_settings_loaded():
    from django.conf import settings

    assert "core" in settings.INSTALLED_APPS
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"


def test_seed_command_creates_contest(seeded_contest):
    assert seeded_contest.year == 2026
    # Third Sunday of July 2026 is the 19th.
    assert seeded_contest.contest_date == date(2026, 7, 19)
    assert seeded_contest.start_utc.hour == 6
    assert seeded_contest.half_split_utc.hour == 8
    assert seeded_contest.end_utc.hour == 9


@pytest.mark.django_db
def test_audit_helper_writes_row(participant_user):
    from core.audit import audit
    from core.models import AuditLog

    entry = audit(actor=participant_user, action="test.action", target="HB9TEST")
    assert entry is not None
    assert AuditLog.objects.filter(action="test.action", target="HB9TEST").exists()


@pytest.mark.django_db
def test_login_page_renders(client):
    response = client.get("/submission/login/")
    assert response.status_code == 200
    assert b"Login" in response.content or b"Anmelden" in response.content


@pytest.mark.django_db
def test_root_redirects_to_portal(client):
    response = client.get("/")
    assert response.status_code in (301, 302)


@pytest.mark.django_db
def test_dashboard_requires_auth(client):
    response = client.get("/submission/")
    # Either a 302 redirect to login or 200 with login form, depending on URL routing.
    assert response.status_code in (302, 301)

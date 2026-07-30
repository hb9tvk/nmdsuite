"""Redaktion (club-magazine editor) role — access control + routing.

An editor is ``is_staff=False`` but a member of the Redaktion group. They
may reach only the publication-prep tools; every other admin surface stays
staff-only.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

from core.roles import REDAKTION_GROUP

User = get_user_model()


def _editor(username="REDAKTOR"):
    user = User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org",
        is_staff=False,
    )
    group, _ = Group.objects.get_or_create(name=REDAKTION_GROUP)
    user.groups.add(group)
    return user


def _staff(username="STAFF"):
    return User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org", is_staff=True,
    )


def _plain(username="HB9JOE"):
    return User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org",
    )


PUBLISH_URLS = [
    "/admin/publish/",
    "/admin/reports/",
    "/admin/ranking-preview/",
    "/admin/ranking.pdf",
]

STAFF_ONLY_URLS = [
    "/admin/",
    "/admin/participants/",
    "/admin/email/",
    "/admin/backup/",
    "/admin/fixstation/",
    "/admin/audit/",
]


@pytest.mark.django_db
@pytest.mark.parametrize("url", PUBLISH_URLS)
def test_editor_can_access_publish_tools(client, seeded_contest, url):
    client.force_login(_editor())
    assert client.get(url).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("url", STAFF_ONLY_URLS)
def test_editor_cannot_access_staff_tools(client, seeded_contest, url):
    client.force_login(_editor())
    assert client.get(url).status_code in (302, 403)


@pytest.mark.django_db
@pytest.mark.parametrize("url", PUBLISH_URLS)
def test_plain_user_cannot_access_publish_tools(client, seeded_contest, url):
    client.force_login(_plain())
    assert client.get(url).status_code in (302, 403)


@pytest.mark.django_db
@pytest.mark.parametrize("url", PUBLISH_URLS)
def test_staff_can_access_publish_tools(client, seeded_contest, url):
    client.force_login(_staff())
    assert client.get(url).status_code == 200


@pytest.mark.django_db
def test_login_routes_editor_to_publish_portal(client, seeded_contest):
    """An editor landing on the participant dashboard is bounced to their
    cut-down publication portal, not the 'you're not registered' screen."""
    client.force_login(_editor())
    response = client.get(reverse("portal:dashboard"))
    assert response.status_code == 302
    assert response.url == reverse("admin_module:publish_index")


@pytest.mark.django_db
def test_publish_index_lists_the_three_tools(client, seeded_contest):
    client.force_login(_editor())
    body = client.get("/admin/publish/").content.decode()
    assert reverse("admin_module:reports_index") in body
    assert reverse("admin_module:ranking_preview") in body
    assert reverse("admin_module:ranking_pdf") in body
    # No staff-only tool leaks onto the editor's portal.
    assert reverse("admin_module:bulk_email") not in body
    assert reverse("admin_module:backup_index") not in body


@pytest.mark.django_db
def test_setup_new_contest_keeps_editors_active(seeded_contest):
    """The annual reset deactivates participant accounts but preserves
    staff, superusers, and Redaktion editors."""
    from admin_module.services import setup_new_contest

    editor = _editor()
    plain = _plain()
    staff = _staff()

    setup_new_contest(year=seeded_contest.year + 1, actor=staff)

    editor.refresh_from_db()
    plain.refresh_from_db()
    assert editor.is_active is True    # editor persists across seasons
    assert plain.is_active is False    # plain participant deactivated
"""Embed mode for iframing into the WordPress site.

``?embed=1`` strips the Django chrome (brand, nav links, login/logout,
footer) so the page can be iframed inside the WP site at nmd.uska.ch
and still show the WP top nav above. The language switcher stays —
it's per-page state, not chrome — and a small postMessage snippet
reports body height to the parent for auto-resize.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import Contest

User = get_user_model()


@pytest.fixture
def published_contest(seeded_contest):
    seeded_contest.state = Contest.State.PUBLISHED
    seeded_contest.results_published_at = timezone.now()
    seeded_contest.save(update_fields=["state", "results_published_at"])
    return seeded_contest


def _registration_body(client) -> str:
    return client.get("/anmeldung/").content.decode()


def _registration_body_embed(client) -> str:
    return client.get("/anmeldung/?embed=1").content.decode()


# --- registration page -----------------------------------------------------------------------


@pytest.mark.django_db
def test_registration_normal_mode_has_full_chrome(client, seeded_contest):
    body = _registration_body(client)
    assert ">NMD</a>" in body                        # brand link
    # Login entry for anonymous visitors (part of the chrome that embed
    # mode strips out).
    assert "Login" in body or "Connexion" in body or "Accesso" in body
    assert "<footer" in body
    assert "nmd-iframe-height" not in body           # auto-resize JS hidden


@pytest.mark.django_db
def test_registration_embed_mode_strips_chrome(client, seeded_contest):
    body = _registration_body_embed(client)
    assert "embed-mode" in body                      # class flag on body
    # Brand link and the public nav links are gone.
    assert ">NMD</a>" not in body
    # Login link is the lone fallback for anonymous users in full chrome.
    assert 'href="/submission/login/"' not in body
    # Footer disappears too.
    assert "<footer" not in body


@pytest.mark.django_db
def test_registration_embed_mode_keeps_language_switcher(client, seeded_contest):
    body = _registration_body_embed(client)
    # The switcher form posts to Django's set_language endpoint.
    assert 'action="/i18n/setlang/"' in body or 'name="language"' in body
    assert 'name="next"' in body


@pytest.mark.django_db
def test_registration_embed_mode_ships_iframe_resize_js(client, seeded_contest):
    body = _registration_body_embed(client)
    assert "nmd-iframe-height" in body
    assert "ResizeObserver" in body


# --- public ranking page ---------------------------------------------------------------------


@pytest.mark.django_db
def test_public_ranking_embed_mode_strips_chrome(client, published_contest):
    body = client.get(f"/ranking/{published_contest.year}/?embed=1").content.decode()
    assert "embed-mode" in body
    assert ">NMD</a>" not in body
    assert "<footer" not in body
    assert "nmd-iframe-height" in body

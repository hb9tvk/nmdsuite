"""Login form normalization tests."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.fixture
def hb9tvk(db):
    return User.objects.create_user(username="HB9TVK", password="strong-pass-1234", email="t@example.org")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "entered_callsign",
    [
        "HB9TVK",
        "hb9tvk",
        "Hb9TvK",
        "  hb9tvk  ",
        "hb9tvk/p",
        "HB9TVK/P",
    ],
)
def test_login_accepts_callsign_variants(client, hb9tvk, entered_callsign):
    response = client.post(
        "/submission/login/",
        {"username": entered_callsign, "password": "strong-pass-1234"},
        follow=False,
    )
    # Successful auth → 302 redirect to LOGIN_REDIRECT_URL.
    assert response.status_code == 302, f"login rejected for variant {entered_callsign!r}"


@pytest.mark.django_db
def test_login_rejects_unknown_callsign(client, hb9tvk):
    response = client.post(
        "/submission/login/",
        {"username": "HB9XXX", "password": "strong-pass-1234"},
    )
    assert response.status_code == 200  # form re-renders with error

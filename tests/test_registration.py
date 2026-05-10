"""Tests for the registration module (M1 slice 1)."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core import mail

from core.models import AuditLog, Contest, EmailLog, Participant
from registration.callsigns import is_valid_callsign, login_username, normalize_callsign

User = get_user_model()


VALID_FORM = {
    "callsign": "HB9TVK/P",
    "first_name": "Peter",
    "email": "peter@example.org",
    "multi_op": "False",
    "station_chief": "",
    "coord_system_input": "wgs84",
    "coord_input_e": "8.2275",
    "coord_input_n": "46.8182",
    "altitude_m": "1500",
    "canton": "BE",
    "mode_cw": "on",
    "mode_ssb": "",
    "remarks": "",
}


# --- callsign helpers --------------------------------------------------------------------------


def test_normalize_callsign_uppercases_and_strips():
    assert normalize_callsign("  hb9tvk/p ") == "HB9TVK/P"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("HB9TVK", True),
        ("HB9TVK/P", True),
        ("OE/HB9TVK/P", True),
        ("HB9TVK/QRP", True),      # /QRP is a recognized portable-power suffix
        ("/HB9TVK", False),
        ("hb9tvk@uska", False),
        ("HB9TVK/", False),
        ("", False),
    ],
)
def test_is_valid_callsign(raw, expected):
    assert is_valid_callsign(raw) is expected


@pytest.mark.parametrize(
    "raw,username",
    [
        ("HB9TVK", "HB9TVK"),
        ("HB9TVK/P", "HB9TVK"),
        ("OE/HB9TVK/P", "OE/HB9TVK"),
        ("hb9tvk", "HB9TVK"),
    ],
)
def test_login_username_strips_trailing_letter_suffix(raw, username):
    assert login_username(raw) == username


# --- view + service ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_registration_renders(client, seeded_contest):
    response = client.get("/anmeldung/")
    assert response.status_code == 200
    assert b"NMD" in response.content


@pytest.mark.django_db
def test_post_valid_creates_user_participant_and_email(client, seeded_contest):
    response = client.post("/anmeldung/", VALID_FORM, follow=True)
    assert response.status_code == 200
    assert b"Thank you" in response.content or b"Danke" in response.content

    user = User.objects.get(username="HB9TVK")
    assert user.email == "peter@example.org"
    assert user.has_usable_password()

    participant = Participant.objects.get(user=user, contest=seeded_contest)
    assert participant.callsign == "HB9TVK/P"
    assert participant.canton == "BE"
    assert participant.operating_modes == 1  # CW only

    assert EmailLog.objects.filter(recipient="peter@example.org", status=EmailLog.Status.SENT).exists()
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert "HB9TVK" in body
    assert "Passwort" in body  # German section always present

    assert AuditLog.objects.filter(action="registration.create", target="HB9TVK/P").exists()


@pytest.mark.django_db
def test_post_missing_mode_is_rejected(client, seeded_contest):
    bad = {**VALID_FORM, "mode_cw": "", "mode_ssb": ""}
    response = client.post("/anmeldung/", bad)
    assert response.status_code == 200
    assert User.objects.filter(username="HB9TVK").count() == 0
    # Form re-renders with errors; "operating mode" copy is on the page.
    assert b"mode" in response.content.lower() or b"Modus" in response.content


@pytest.mark.django_db
def test_post_multi_op_requires_station_chief(client, seeded_contest):
    bad = {**VALID_FORM, "multi_op": "True", "station_chief": ""}
    response = client.post("/anmeldung/", bad)
    assert response.status_code == 200
    assert User.objects.filter(username="HB9TVK").count() == 0


@pytest.mark.django_db
def test_invalid_callsign_rejected(client, seeded_contest):
    bad = {**VALID_FORM, "callsign": "not-a-call"}
    response = client.post("/anmeldung/", bad)
    assert response.status_code == 200
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_returning_participant_keeps_password(client, seeded_contest):
    """Same callsign, different year: account reused, password unchanged, no new password emailed."""
    # Pre-existing user with a known password from a prior contest.
    existing = User.objects.create_user(
        username="HB9TVK",
        email="old@example.org",
        password="prior-password-123",
        first_name="Peter",
    )
    # Cancel any prior participation so we don't violate the (contest, user) unique constraint.
    # In this test the prior contest doesn't even exist; we just verify the account is reused.
    response = client.post("/anmeldung/", VALID_FORM, follow=True)
    assert response.status_code == 200

    existing.refresh_from_db()
    assert existing.check_password("prior-password-123"), "Returning operator's password must not change"
    assert existing.email == "peter@example.org", "Contact details should be refreshed"

    body = mail.outbox[-1].body
    # Password block must NOT appear when account already existed.
    assert "Passwort:" not in body
    # Reset link should be present instead.
    assert "/submission/password-reset/" in body


@pytest.mark.django_db
def test_duplicate_registration_for_same_contest_is_rejected(client, seeded_contest):
    client.post("/anmeldung/", VALID_FORM)
    response = client.post("/anmeldung/", VALID_FORM)
    assert response.status_code == 200
    assert Participant.objects.filter(contest=seeded_contest, user__username="HB9TVK").count() == 1


@pytest.mark.django_db
def test_registration_closed_state_blocks_new_signups(client, seeded_contest):
    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save()
    response = client.get("/anmeldung/")
    assert response.status_code == 200
    assert b"closed" in response.content.lower() or b"geschlossen" in response.content.lower()

    response = client.post("/anmeldung/", VALID_FORM)
    assert User.objects.filter(username="HB9TVK").count() == 0


@pytest.mark.django_db
def test_no_active_contest_renders_closed(client):
    response = client.get("/anmeldung/")
    assert response.status_code == 200
    assert b"closed" in response.content.lower() or b"keinen aktiven" in response.content.lower() or b"no active" in response.content.lower()

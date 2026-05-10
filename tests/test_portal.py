"""Participant portal tests (M2.1: dashboard / edit / cancel)."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import AuditLog, Participant

User = get_user_model()


@pytest.fixture
def registered_user(seeded_contest):
    """A logged-in operator with an active registration in seeded_contest."""
    user = User.objects.create_user(
        username="HB9TVK",
        email="peter@example.org",
        password="strong-pass-1234",
        first_name="Peter",
    )
    p = Participant.objects.create(
        contest=seeded_contest,
        user=user,
        callsign="HB9TVK/P",
        first_name="Peter",
        email="peter@example.org",
        coord_system_input="wgs84",
        coord_input_e="8.2275",
        coord_input_n="46.8182",
        wgs84_lat=46.8182,
        wgs84_lon=8.2275,
        ch1903p_e=2_605_000,
        ch1903p_n=1_200_000,
        altitude_m=1500,
        canton="BE",
        operating_modes=3,
    )
    return user, p


# --- dashboard --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_dashboard_requires_login(client):
    response = client.get("/submission/")
    assert response.status_code in (301, 302)
    assert "/submission/login/" in response["Location"]


@pytest.mark.django_db
def test_dashboard_for_unregistered_user(client, seeded_contest):
    other = User.objects.create_user(username="HB9XYZ", password="strong-pass-1234")
    client.force_login(other)
    response = client.get("/submission/")
    assert response.status_code == 200
    # No participation yet — should hint at registration.
    assert b"not registered" in response.content.lower() or b"register" in response.content.lower()


@pytest.mark.django_db
def test_dashboard_for_registered_user_shows_data(client, registered_user):
    user, participant = registered_user
    client.force_login(user)
    response = client.get("/submission/")
    assert response.status_code == 200
    assert b"HB9TVK/P" in response.content
    assert b"BE" in response.content
    assert b"1500" in response.content


# --- edit -------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_edit_get_prefills_with_current_data(client, registered_user):
    user, participant = registered_user
    client.force_login(user)
    response = client.get("/submission/profile/edit/")
    assert response.status_code == 200
    assert b"8.2275" in response.content
    assert b"46.8182" in response.content
    # Immutable fields are shown as plain text, not as form inputs.
    assert b"HB9TVK/P" in response.content
    assert b'name="callsign"' not in response.content
    assert b'name="email"' not in response.content


@pytest.mark.django_db
def test_edit_post_updates_only_editable_fields(client, registered_user):
    user, participant = registered_user
    client.force_login(user)
    response = client.post(
        "/submission/profile/edit/",
        {
            "multi_op": "True",
            "station_chief": "HB9XYZ",
            "coord_input_e": "7.4474",
            "coord_input_n": "46.9480",
            "altitude_m": "950",
            "canton": "BE",
            "mode_cw": "on",
            "mode_ssb": "",
            "remarks": "moved location",
        },
        follow=False,
    )
    assert response.status_code == 302  # redirect to dashboard

    participant.refresh_from_db()
    assert participant.multi_op is True
    assert participant.station_chief == "HB9XYZ"
    assert participant.altitude_m == 950
    assert participant.operating_modes == 1  # CW only
    assert participant.remarks == "moved location"
    assert participant.wgs84_lat == pytest.approx(46.9480, abs=1e-4)
    # Identity fields untouched.
    assert participant.callsign == "HB9TVK/P"
    assert participant.first_name == "Peter"
    assert participant.email == "peter@example.org"

    assert AuditLog.objects.filter(action="registration.update", target="HB9TVK/P").exists()


@pytest.mark.django_db
def test_edit_rejects_altitude_below_800(client, registered_user):
    user, participant = registered_user
    client.force_login(user)
    response = client.post(
        "/submission/profile/edit/",
        {
            "multi_op": "False",
            "station_chief": "",
            "coord_input_e": "8.2275",
            "coord_input_n": "46.8182",
            "altitude_m": "600",
            "canton": "BE",
            "mode_cw": "on",
            "mode_ssb": "",
            "remarks": "",
        },
    )
    assert response.status_code == 200  # form re-rendered with errors
    participant.refresh_from_db()
    assert participant.altitude_m == 1500  # unchanged


@pytest.mark.django_db
def test_edit_redirects_when_not_registered(client, seeded_contest):
    other = User.objects.create_user(username="HB9XYZ", password="strong-pass-1234")
    client.force_login(other)
    response = client.get("/submission/profile/edit/")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")


# --- cancel -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_cancel_get_shows_confirmation(client, registered_user):
    user, participant = registered_user
    client.force_login(user)
    response = client.get("/submission/profile/cancel/")
    assert response.status_code == 200
    assert b"HB9TVK/P" in response.content
    # No POST has been issued yet — participation must still be active.
    participant.refresh_from_db()
    assert participant.cancelled_at is None


@pytest.mark.django_db
def test_cancel_post_marks_participant_cancelled_and_logs_out(client, registered_user):
    user, participant = registered_user
    client.force_login(user)
    response = client.post("/submission/profile/cancel/")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/login/")

    participant.refresh_from_db()
    assert participant.cancelled_at is not None
    # The user account itself remains so re-registration in a future contest works.
    assert User.objects.filter(username="HB9TVK").exists()

    # Subsequent dashboard visit should treat them as unregistered.
    client.force_login(user)
    response = client.get("/submission/")
    assert response.status_code == 200
    assert b"not registered" in response.content.lower() or b"register" in response.content.lower()

    assert AuditLog.objects.filter(action="registration.cancel", target="HB9TVK/P").exists()

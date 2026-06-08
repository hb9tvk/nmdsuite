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
    "location_text": "Niederhorn",
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
    assert participant.callsign == "HB9TVK"
    assert participant.canton == "BE"
    assert participant.operating_modes == 1  # CW only

    # Coordinate canonicalization: input was WGS84, all four canonical fields populated.
    assert participant.coord_system_input == "wgs84"
    assert participant.wgs84_lat == pytest.approx(46.8182, abs=1e-4)
    assert participant.wgs84_lon == pytest.approx(8.2275, abs=1e-4)
    assert participant.ch1903p_e is not None
    assert participant.ch1903p_n is not None

    assert EmailLog.objects.filter(recipient="peter@example.org", status=EmailLog.Status.SENT).exists()
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert "HB9TVK" in body
    assert "Passwort" in body  # German section always present

    assert AuditLog.objects.filter(action="registration.create", target="HB9TVK").exists()


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
@pytest.mark.parametrize("altitude", ["799", "0", "100"])
def test_post_rejects_altitude_below_800m(client, seeded_contest, altitude):
    bad = {**VALID_FORM, "altitude_m": altitude}
    response = client.post("/anmeldung/", bad)
    assert response.status_code == 200
    assert User.objects.count() == 0
    # The translatable error string is what the form re-renders with.
    assert b"800 m" in response.content


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
@pytest.mark.parametrize(
    "e_input,n_input,expected_system",
    [
        ("8.2275", "46.8182", "wgs84"),
        ("2660000", "1190000", "ch1903plus"),
        ("660000", "190000", "ch1903"),
        ("2'660'000", "1'190'000", "ch1903plus"),
    ],
)
def test_post_accepts_each_coordinate_system(client, seeded_contest, e_input, n_input, expected_system):
    payload = {**VALID_FORM, "coord_input_e": e_input, "coord_input_n": n_input}
    response = client.post("/anmeldung/", payload, follow=True)
    assert response.status_code == 200
    p = Participant.objects.get(user__username="HB9TVK", contest=seeded_contest)
    assert p.coord_system_input == expected_system
    assert p.wgs84_lat is not None and p.wgs84_lon is not None


@pytest.mark.django_db
def test_post_rejects_coordinates_outside_switzerland(client, seeded_contest):
    bad = {**VALID_FORM, "coord_input_e": "2.3522", "coord_input_n": "48.8566"}  # Paris
    response = client.post("/anmeldung/", bad)
    assert response.status_code == 200
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_no_active_contest_renders_closed(client):
    response = client.get("/anmeldung/")
    assert response.status_code == 200
    assert b"closed" in response.content.lower() or b"keinen aktiven" in response.content.lower() or b"no active" in response.content.lower()


# --- QRB proximity warning ------------------------------------------------------------------


def _make_neighbour(contest, *, callsign, lv95_e, lv95_n):
    """Create an active neighbouring participant at the given LV95 coords."""
    from django.utils import timezone
    user = User.objects.create_user(
        username=callsign, password="x", email=f"{callsign.lower()}@x.org",
    )
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign,
        first_name=callsign, email=f"{callsign.lower()}@x.org",
        coord_system_input="ch1903plus",
        coord_input_e=str(lv95_e), coord_input_n=str(lv95_n),
        ch1903p_e=lv95_e, ch1903p_n=lv95_n,
        wgs84_lat=46.8, wgs84_lon=8.2,
        altitude_m=1500, canton="BE", operating_modes=3,
    )


# Drive the QRB tests with CH1903+ (LV95) coord input so the form's
# parsed LV95 is exactly what we type — no WGS84-conversion ambiguity to
# fight when placing a neighbour at a known offset.
_QRB_E = 2_681_000
_QRB_N = 1_237_000
_QRB_FORM = {**VALID_FORM, "coord_input_e": str(_QRB_E), "coord_input_n": str(_QRB_N)}


@pytest.mark.django_db
def test_post_within_3km_without_ack_is_rejected_and_lists_neighbour(client, seeded_contest):
    _make_neighbour(
        seeded_contest, callsign="HB9CLOSE",
        lv95_e=_QRB_E + 1000, lv95_n=_QRB_N,
    )
    response = client.post("/anmeldung/", _QRB_FORM)
    assert response.status_code == 200  # form re-rendered with errors
    # No new account created — registration didn't go through.
    assert not User.objects.filter(username="HB9TVK").exists()
    # Banner with the close neighbour shows up.
    body = response.content.decode()
    assert "HB9CLOSE" in body


@pytest.mark.django_db
def test_post_within_3km_with_ack_succeeds(client, seeded_contest):
    _make_neighbour(
        seeded_contest, callsign="HB9CLOSE",
        lv95_e=_QRB_E + 1000, lv95_n=_QRB_N,
    )
    response = client.post(
        "/anmeldung/",
        {**_QRB_FORM, "qrb_acknowledged": "on"},
        follow=True,
    )
    assert response.status_code == 200
    assert User.objects.filter(username="HB9TVK").exists()


@pytest.mark.django_db
def test_post_far_neighbour_does_not_trigger_warning(client, seeded_contest):
    _make_neighbour(
        seeded_contest, callsign="HB9FAR",
        lv95_e=_QRB_E + 5000, lv95_n=_QRB_N,
    )
    response = client.post("/anmeldung/", _QRB_FORM, follow=True)
    assert response.status_code == 200
    assert User.objects.filter(username="HB9TVK").exists()


@pytest.mark.django_db
def test_qrb_check_skips_cancelled_neighbours(client, seeded_contest):
    """A cancelled participant doesn't physically occupy the location any
    more — don't pretend they're a conflict."""
    n = _make_neighbour(
        seeded_contest, callsign="HB9CANC",
        lv95_e=_QRB_E + 1000, lv95_n=_QRB_N,
    )
    from django.utils import timezone
    n.cancelled_at = timezone.now()
    n.save(update_fields=["cancelled_at"])

    response = client.post("/anmeldung/", _QRB_FORM, follow=True)
    assert response.status_code == 200
    assert User.objects.filter(username="HB9TVK").exists()


# --- re-register after cancel -----------------------------------------------------------------


@pytest.mark.django_db
def test_can_register_again_after_cancelling_in_same_contest(client, seeded_contest):
    """The cancelled Participant row + UniqueConstraint(contest, user)
    used to block re-registration with the 'already registered' error.
    The new flow drops the cancelled row first so the operator gets a
    clean fresh registration."""
    from registration.services import cancel_participation

    # First registration goes through normally.
    response = client.post("/anmeldung/", VALID_FORM, follow=True)
    assert response.status_code == 200
    p = Participant.objects.get(user__username="HB9TVK", contest=seeded_contest)

    # Operator cancels.
    cancel_participation(p)
    p.refresh_from_db()
    assert p.cancelled_at is not None

    # Second registration with the same callsign should succeed and land
    # as a fresh active Participant — same user, new row.
    response = client.post("/anmeldung/", VALID_FORM, follow=True)
    assert response.status_code == 200
    active = Participant.objects.filter(
        user__username="HB9TVK", contest=seeded_contest, cancelled_at__isnull=True,
    )
    assert active.count() == 1
    # The cancelled row was swept; only the new active one remains.
    assert Participant.objects.filter(
        user__username="HB9TVK", contest=seeded_contest,
    ).count() == 1

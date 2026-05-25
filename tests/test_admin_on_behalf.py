"""Administration module — M4.3a on-behalf participant management.

Covers:
- registration.services.register_participant / update_participant_profile
  with an ``actor=`` override (audit row attributed to the staff user,
  ``on_behalf=True`` flag in the payload).
- /admin/participants/ index + filters.
- /admin/participants/register/ — on-behalf registration, bypasses the
  contest state check.
- /admin/participants/<callsign>/ — detail page.
- /admin/participants/<callsign>/edit-profile/ — on-behalf edit,
  bypasses the submitted_at lock.
- Staff-only access on every new URL.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import AuditLog, Contest, Participant
from registration.services import register_participant

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


def _make_staff_user(username: str = "STAFF") -> User:
    return User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org", is_staff=True,
    )


def _make_participant(contest, *, username, callsign, submitted=False, cancelled=False) -> Participant:
    user = User.objects.create_user(username=username, password="x", email=f"{username.lower()}@x.org")
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign, first_name=username,
        email=f"{username.lower()}@x.org", coord_system_input="wgs84",
        coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2, ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", location_text="Niederhorn", operating_modes=3,
        submitted_at=timezone.now() if submitted else None,
        cancelled_at=timezone.now() if cancelled else None,
    )


# --- service layer: actor override ----------------------------------------------------------


@pytest.mark.django_db
def test_register_participant_default_actor_is_participant_user(seeded_contest):
    from registration.coords import parse_coordinate_pair

    form_data = {
        "callsign": "HB9TVK",
        "first_name": "Peter",
        "email": "p@x.org",
        "multi_op": False,
        "station_chief": "",
        "coord_input_e": "8.2275",
        "coord_input_n": "46.8182",
        "parsed_coords": parse_coordinate_pair("8.2275", "46.8182"),
        "altitude_m": 1500,
        "canton": "BE",
        "operating_modes": 1,
        "remarks": "",
    }
    outcome = register_participant(contest=seeded_contest, form_data=form_data)

    entry = AuditLog.objects.get(action="registration.create", target="HB9TVK")
    assert entry.actor == outcome.participant.user
    assert "on_behalf" not in entry.payload


@pytest.mark.django_db
def test_register_participant_with_staff_actor_records_on_behalf(seeded_contest):
    from registration.coords import parse_coordinate_pair

    staff = _make_staff_user()
    form_data = {
        "callsign": "HB9TVK",
        "first_name": "Peter",
        "email": "p@x.org",
        "multi_op": False,
        "station_chief": "",
        "coord_input_e": "8.2275",
        "coord_input_n": "46.8182",
        "parsed_coords": parse_coordinate_pair("8.2275", "46.8182"),
        "altitude_m": 1500,
        "canton": "BE",
        "operating_modes": 1,
        "remarks": "",
    }
    register_participant(contest=seeded_contest, form_data=form_data, actor=staff)

    entry = AuditLog.objects.get(action="registration.create", target="HB9TVK")
    assert entry.actor == staff
    assert entry.payload.get("on_behalf") is True


# --- access control --------------------------------------------------------------------------


@pytest.mark.django_db
def test_participants_index_redirects_non_staff(client, seeded_contest):
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.get("/admin/participants/")
    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_participant_register_redirects_non_staff(client, seeded_contest):
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.get("/admin/participants/register/")
    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_participant_detail_redirects_non_staff(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.get(f"/admin/participants/{p.pk}/")
    assert response.status_code in (302, 403)


# --- index -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_index_lists_participants(client, seeded_contest):
    _make_participant(seeded_contest, username="HB9AAA", callsign="HB9AAA")
    _make_participant(seeded_contest, username="HB9BBB", callsign="HB9BBB", submitted=True)
    client.force_login(_make_staff_user())

    response = client.get("/admin/participants/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "HB9AAA" in body
    assert "HB9BBB" in body
    assert "pending" in body
    assert "submitted" in body


@pytest.mark.django_db
def test_index_filter_by_callsign(client, seeded_contest):
    _make_participant(seeded_contest, username="HB9AAA", callsign="HB9AAA")
    _make_participant(seeded_contest, username="HB9BBB", callsign="HB9BBB")
    client.force_login(_make_staff_user())

    response = client.get("/admin/participants/?callsign=AAA")
    body = response.content.decode()
    assert "HB9AAA" in body
    assert "HB9BBB" not in body


@pytest.mark.django_db
def test_index_filter_by_status(client, seeded_contest):
    _make_participant(seeded_contest, username="HB9AAA", callsign="HB9AAA")
    _make_participant(seeded_contest, username="HB9BBB", callsign="HB9BBB", submitted=True)
    _make_participant(seeded_contest, username="HB9CCC", callsign="HB9CCC", cancelled=True)
    client.force_login(_make_staff_user())

    body = client.get("/admin/participants/?status=submitted").content.decode()
    assert "HB9BBB" in body and "HB9AAA" not in body and "HB9CCC" not in body

    body = client.get("/admin/participants/?status=pending").content.decode()
    assert "HB9AAA" in body and "HB9BBB" not in body and "HB9CCC" not in body

    body = client.get("/admin/participants/?status=cancelled").content.decode()
    assert "HB9CCC" in body and "HB9AAA" not in body and "HB9BBB" not in body


# --- on-behalf registration ------------------------------------------------------------------


@pytest.mark.django_db
def test_register_get_renders_form(client, seeded_contest):
    client.force_login(_make_staff_user())
    response = client.get("/admin/participants/register/")
    assert response.status_code == 200
    assert b"Callsign" in response.content


@pytest.mark.django_db
def test_register_post_creates_participant_attributed_to_staff(client, seeded_contest):
    staff = _make_staff_user()
    client.force_login(staff)
    response = client.post("/admin/participants/register/", VALID_FORM)

    assert response.status_code == 302
    participant = Participant.objects.get(callsign="HB9TVK")
    assert participant.contest == seeded_contest
    # Redirect lands on the detail page for this participant.
    assert response["Location"].endswith(f"/admin/participants/{participant.pk}/")

    entry = AuditLog.objects.get(action="registration.create", target="HB9TVK")
    assert entry.actor == staff
    assert entry.payload.get("on_behalf") is True


@pytest.mark.django_db
def test_register_bypasses_state_check(client, seeded_contest):
    """Registration in admin works even after the contest's registration
    has been closed — that's the whole point of the on-behalf surface."""
    seeded_contest.state = Contest.State.REGISTRATION_CLOSED
    seeded_contest.save(update_fields=["state"])

    client.force_login(_make_staff_user())
    response = client.post("/admin/participants/register/", VALID_FORM)
    assert response.status_code == 302
    assert Participant.objects.filter(callsign="HB9TVK").exists()


# --- detail ----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_detail_renders_participant_info(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    client.force_login(_make_staff_user())
    response = client.get(f"/admin/participants/{p.pk}/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "HB9XYZ" in body


@pytest.mark.django_db
def test_detail_handles_slash_in_callsign(client, seeded_contest):
    """Pk-keyed URLs sidestep slash-in-callsign URL routing issues.
    Even though /P is stripped at registration today, country prefixes
    like ``OE/HB9TVK`` still keep a slash and must route safely."""
    p = _make_participant(seeded_contest, username="OE/HB9TVK", callsign="OE/HB9TVK")
    client.force_login(_make_staff_user())
    response = client.get(f"/admin/participants/{p.pk}/")
    assert response.status_code == 200
    assert "OE/HB9TVK" in response.content.decode()


@pytest.mark.django_db
def test_detail_404_for_unknown_pk(client, seeded_contest):
    client.force_login(_make_staff_user())
    response = client.get("/admin/participants/9999/")
    assert response.status_code == 404


# Note: M4B (merge-station-into-participant) removed the dedicated
# edit-profile views. Equivalent on-behalf coverage now lives on the
# unified station-data flow in test_admin_on_behalf_b.py.

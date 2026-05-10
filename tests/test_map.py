"""Tests for the map picker (slice 3): JSON endpoint + template wiring."""
from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import Participant

User = get_user_model()


def _make_participant(contest, *, callsign, lat=46.8, lon=8.2, cancelled=False):
    user = User.objects.create_user(
        username=callsign.replace("/P", ""),
        email=f"{callsign.lower().replace('/', '-')}@example.org",
        password="x",
    )
    p = Participant.objects.create(
        contest=contest,
        user=user,
        callsign=callsign,
        first_name="Test",
        email=user.email,
        coord_system_input="wgs84",
        coord_input_e=str(lon),
        coord_input_n=str(lat),
        wgs84_lat=lat,
        wgs84_lon=lon,
        ch1903p_e=2_600_000,
        ch1903p_n=1_200_000,
        altitude_m=1000,
        canton="BE",
        operating_modes=3,
    )
    if cancelled:
        p.cancelled_at = timezone.now()
        p.save()
    return p


@pytest.mark.django_db
def test_registrations_json_empty_when_no_contest(client):
    response = client.get("/anmeldung/registrations.json")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["contest"] is None
    assert body["participants"] == []


@pytest.mark.django_db
def test_registrations_json_returns_active_contest_participants(client, seeded_contest):
    _make_participant(seeded_contest, callsign="HB9TVK/P", lat=46.97, lon=8.25)
    _make_participant(seeded_contest, callsign="HB9ABC/P", lat=46.55, lon=7.43)
    response = client.get("/anmeldung/registrations.json")
    body = json.loads(response.content)
    assert body["contest"] == seeded_contest.year
    callsigns = {p["callsign"] for p in body["participants"]}
    assert callsigns == {"HB9TVK/P", "HB9ABC/P"}
    sample = body["participants"][0]
    assert {"callsign", "lat", "lon", "altitude_m", "canton"} <= sample.keys()
    # Email and station details must NOT be exposed.
    assert "email" not in sample
    assert "first_name" not in sample


@pytest.mark.django_db
def test_registrations_json_excludes_cancelled(client, seeded_contest):
    _make_participant(seeded_contest, callsign="HB9TVK/P")
    _make_participant(seeded_contest, callsign="HB9ZZZ/P", cancelled=True)
    response = client.get("/anmeldung/registrations.json")
    body = json.loads(response.content)
    callsigns = {p["callsign"] for p in body["participants"]}
    assert callsigns == {"HB9TVK/P"}


@pytest.mark.django_db
def test_index_template_embeds_map_container(client, seeded_contest):
    response = client.get("/anmeldung/")
    assert response.status_code == 200
    assert b'id="reg-map"' in response.content
    # Leaflet asset references and the inline config block should be present.
    assert b"leaflet.js" in response.content
    assert b"NMDMapConfig" in response.content
    assert b"registrations.json" in response.content


@pytest.mark.django_db
def test_index_template_includes_altitude_lookup_wiring(client, seeded_contest):
    response = client.get("/anmeldung/")
    assert response.status_code == 200
    # The altitude info DOM node and the height-API URL must both be present
    # for the client-side altitude lookup to work.
    assert b'id="altitude-info"' in response.content
    assert b"altitudeInputId" in response.content
    assert b"heightApi" in response.content
    assert b"api3.geo.admin.ch" in response.content


@pytest.mark.django_db
def test_index_template_includes_canton_lookup_wiring(client, seeded_contest):
    response = client.get("/anmeldung/")
    assert response.status_code == 200
    assert b"cantonInputId" in response.content
    assert b"identifyApi" in response.content
    assert b"MapServer/identify" in response.content

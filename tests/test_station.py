"""Station description (M2.4) — service + view tests."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import AuditLog, Participant, StationComponent, StationDescription
from portal import station_service

User = get_user_model()


@pytest.fixture
def participant(seeded_contest):
    user = User.objects.create_user(
        username="HB9TVK", password="x", email="t@example.org", first_name="P",
    )
    p = Participant.objects.create(
        contest=seeded_contest, user=user, callsign="HB9TVK/P", first_name="P",
        email="t@example.org", coord_system_input="wgs84",
        coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2, ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", operating_modes=3,
    )
    return user, p


# --- service ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_save_station_creates_description_and_components(participant):
    user, p = participant
    data = {
        "op_name": "Peter",
        "location_text": "Pilatus",
        "watt": "5",
        "sta01bez": "Sender + RX (FT-817)",
        "sta01gramm": 1200,
        "sta02bez": "Battery",
        "sta02gramm": 800,
        "sta03bez": "",
        "sta03gramm": 0,
    }
    station = station_service.save_station(participant=p, data=data)
    assert station.op_name == "Peter"
    assert station.location_text == "Pilatus"
    assert station.total_weight_g == 2000
    components = list(station.components.order_by("idx"))
    assert [c.idx for c in components] == [1, 2]
    assert components[0].description == "Sender + RX (FT-817)"
    assert components[1].weight_g == 800
    assert AuditLog.objects.filter(action="station.update", target="HB9TVK/P").exists()


@pytest.mark.django_db
def test_save_station_replaces_components_on_re_save(participant):
    user, p = participant
    station_service.save_station(
        participant=p,
        data={"sta01bez": "Old", "sta01gramm": 500, "sta02bez": "Other", "sta02gramm": 300},
    )
    station = station_service.save_station(
        participant=p,
        data={"sta01bez": "New only", "sta01gramm": 1000},
    )
    components = list(station.components.all())
    assert len(components) == 1
    assert components[0].description == "New only"
    assert station.total_weight_g == 1000


@pytest.mark.django_db
def test_initial_from_station_round_trips(participant):
    user, p = participant
    station_service.save_station(
        participant=p,
        data={
            "op_name": "Peter", "location_text": "Pilatus", "watt": "5",
            "sta01bez": "TX/RX", "sta01gramm": 1200,
            "sta05bez": "Antenna", "sta05gramm": 350,
        },
    )
    station = StationDescription.objects.get(participant=p)
    init = station_service.initial_from_station(station)
    assert init["op_name"] == "Peter"
    assert init["sta01bez"] == "TX/RX"
    assert init["sta01gramm"] == 1200
    assert init["sta05bez"] == "Antenna"
    assert init["sta05gramm"] == 350
    assert "sta02bez" not in init


# --- view ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_station_get_renders_form_with_fixed_labels(client, participant):
    user, p = participant
    client.force_login(user)
    response = client.get("/submission/station/")
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "station-form" in body
    assert "station-total-display" in body
    # 11 component slots present.
    assert body.count('name="sta') == 22  # bez + gramm per slot
    # Fixed semantic labels from the legacy app, not generic "Component N".
    assert "Transceiver" in body
    assert "Power supply" in body
    assert "Antenna" in body


@pytest.mark.django_db
def test_station_get_displays_participant_location_readonly(client, participant):
    user, p = participant
    client.force_login(user)
    response = client.get("/submission/station/")
    body = response.content.decode("utf-8")
    # Canton + altitude come from Participant (registration) — not editable here.
    assert 'value="BE"' in body
    assert 'value="1500"' in body
    # Coordinates are displayed in CH1903 (LV03) — derived from ch1903p_e/n
    # (2_600_000 - 2_000_000 = 600_000; 1_200_000 - 1_000_000 = 200_000).
    assert 'value="600000"' in body
    assert 'value="200000"' in body
    # Email is displayed read-only from Participant.
    assert "t@example.org" in body


@pytest.mark.django_db
def test_station_get_prefills_from_saved_data(client, participant):
    user, p = participant
    client.force_login(user)
    station_service.save_station(
        participant=p,
        data={"op_name": "Peter", "location_text": "Pilatus", "sta01bez": "TX/RX", "sta01gramm": 1200},
    )
    response = client.get("/submission/station/")
    body = response.content.decode("utf-8")
    assert 'value="Peter"' in body
    assert 'value="Pilatus"' in body
    assert 'value="TX/RX"' in body
    assert 'value="1200"' in body


@pytest.mark.django_db
def test_station_post_saves_form_and_audits(client, participant):
    user, p = participant
    client.force_login(user)
    response = client.post(
        "/submission/station/",
        {
            "op_name": "Peter", "location_text": "Pilatus", "watt": "5",
            "sta01bez": "TX/RX", "sta01gramm": "1200",
            "sta02bez": "Battery", "sta02gramm": "800",
        },
    )
    assert response.status_code == 302  # redirect after save
    station = StationDescription.objects.get(participant=p)
    assert station.op_name == "Peter"
    assert station.total_weight_g == 2000
    assert StationComponent.objects.filter(station=station).count() == 2


@pytest.mark.django_db
def test_station_submitted_state_is_read_only(client, participant):
    user, p = participant
    from django.utils import timezone
    p.submitted_at = timezone.now()
    p.save(update_fields=["submitted_at"])
    client.force_login(user)
    response = client.get("/submission/station/")
    body = response.content.decode("utf-8")
    # The disabled <fieldset> wrapper blocks all child inputs in one go.
    assert "<fieldset disabled>" in body
    # No save button in submitted state.
    assert "Save station description" not in body


@pytest.mark.django_db
def test_station_requires_login(client):
    response = client.get("/submission/station/")
    assert response.status_code in (301, 302)


@pytest.mark.django_db
def test_station_redirects_unregistered_user(client, seeded_contest):
    other = User.objects.create_user(username="HB9XYZ", password="x")
    client.force_login(other)
    response = client.get("/submission/station/")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")


# --- upload integration ----------------------------------------------------------------------


@pytest.mark.django_db
def test_upload_populates_station_description_from_nmd_comments(client, participant):
    user, p = participant
    client.force_login(user)
    blob = (
        b"#;OPNAME=;Peter\n"
        b"#;ORT=;Pilatus\n"
        b"#;WATT=;5\n"
        b"#;STA01BEZ=;Sender + RX (FT-817)\n"
        b"#;STA01GRAMM=;1200\n"
        b"#;STA02BEZ=;Battery\n"
        b"#;STA02GRAMM=;800\n"
        b"0612;HB9ABO/P;599;tragtaschenhandel;599;bitte abstand halten\n"
    )
    response = client.post(
        "/submission/log/upload/",
        {"file": SimpleUploadedFile("log.nmd", blob, content_type="text/csv")},
    )
    # Upload view redirects to dashboard after applying the file.
    assert response.status_code == 302
    station = StationDescription.objects.get(participant=p)
    assert station.op_name == "Peter"
    assert station.location_text == "Pilatus"
    assert station.watt == "5"
    assert station.total_weight_g == 2000
    components = list(station.components.order_by("idx"))
    assert components[0].description == "Sender + RX (FT-817)"
    assert components[1].weight_g == 800


@pytest.mark.django_db
def test_upload_ignores_qah_from_nmd_comments(participant):
    """QAH is not stored on StationDescription. Altitude only changes when
    KOORD_X/KOORD_Y are present (and then it comes from Swisstopo, not from
    the file's own QAH value)."""
    user, p = participant
    original_altitude = p.altitude_m
    station_service.apply_upload_station_info(p, {"OPNAME": "Peter", "QAH": "9999"})
    p.refresh_from_db()
    assert p.altitude_m == original_altitude
    station = StationDescription.objects.get(participant=p)
    assert station.op_name == "Peter"


@pytest.mark.django_db
def test_upload_with_coords_updates_participant_and_refetches_altitude_canton(participant):
    """KOORD_X/KOORD_Y in the .nmd file replace the participant's location;
    altitude comes from Swisstopo (NOT the file's QAH) and canton from
    Swisstopo identify (NOT the file's KANTON)."""
    user, p = participant
    p.altitude_m = 1500
    p.canton = "BE"
    p.save()

    with patch("portal.station_service.swisstopo.lookup_altitude", return_value=2222) as alt, \
            patch("portal.station_service.swisstopo.lookup_canton", return_value="UR") as cant:
        outcome = station_service.apply_upload_station_info(
            p,
            {
                "KOORD_X": "2700000", "KOORD_Y": "1180000",
                "QAH": "9999",        # ignored — Swisstopo is authoritative
                "KANTON": "ZH",       # ignored — Swisstopo is authoritative
                "OPNAME": "Peter",
            },
        )

    assert outcome.location_updated is True
    assert outcome.location_invalid is False

    p.refresh_from_db()
    assert p.altitude_m == 2222  # from Swisstopo, not from QAH=9999
    assert p.canton == "UR"      # from Swisstopo, not from KANTON=ZH
    assert p.coord_input_e == "2700000"
    assert p.coord_input_n == "1180000"
    assert alt.called and cant.called
    # Location change is auditable.
    audit = AuditLog.objects.filter(action="registration.update", target="HB9TVK/P").first()
    assert audit is not None
    assert audit.payload.get("source") == "nmd_upload"


@pytest.mark.django_db
def test_upload_with_invalid_coords_keeps_participant_unchanged(participant):
    """KOORD_X/KOORD_Y outside Switzerland (or unparseable) flag the
    outcome but leave the registered location alone."""
    user, p = participant
    p.altitude_m = 1500
    p.canton = "BE"
    original_e = p.coord_input_e
    p.save()

    with patch("portal.station_service.swisstopo.lookup_altitude") as alt, \
            patch("portal.station_service.swisstopo.lookup_canton") as cant:
        outcome = station_service.apply_upload_station_info(
            p, {"KOORD_X": "not a number", "KOORD_Y": "??", "OPNAME": "Peter"},
        )

    assert outcome.location_updated is False
    assert outcome.location_invalid is True
    assert not alt.called  # never reached Swisstopo
    assert not cant.called

    p.refresh_from_db()
    assert p.altitude_m == 1500
    assert p.canton == "BE"
    assert p.coord_input_e == original_e


@pytest.mark.django_db
def test_upload_swisstopo_failure_keeps_previous_altitude_canton(participant):
    """If Swisstopo is unreachable, coordinates still update but altitude
    and canton fall back to whatever the participant had before."""
    user, p = participant
    p.altitude_m = 1500
    p.canton = "BE"
    p.save()

    with patch("portal.station_service.swisstopo.lookup_altitude", return_value=None), \
            patch("portal.station_service.swisstopo.lookup_canton", return_value=None):
        outcome = station_service.apply_upload_station_info(
            p, {"KOORD_X": "2700000", "KOORD_Y": "1180000"},
        )

    assert outcome.location_updated is True
    p.refresh_from_db()
    assert p.altitude_m == 1500  # kept
    assert p.canton == "BE"      # kept
    # But coords did move.
    assert p.coord_input_e == "2700000"


@pytest.mark.django_db
def test_upload_view_passes_coord_change_through(client, participant):
    """End-to-end: dashboard-style upload with KOORD lines should move the
    participant and report the change in flash messages."""
    user, p = participant
    client.force_login(user)
    blob = (
        b"#;KOORD_X=;2700000\n"
        b"#;KOORD_Y=;1180000\n"
        b"#;OPNAME=;Peter\n"
        b"0612;HB9ABO/P;599;tragtaschenhandel;599;bitte abstand halten\n"
    )
    with patch("portal.station_service.swisstopo.lookup_altitude", return_value=2222), \
            patch("portal.station_service.swisstopo.lookup_canton", return_value="UR"):
        response = client.post(
            "/submission/log/upload/",
            {"file": SimpleUploadedFile("log.nmd", blob, content_type="text/csv")},
        )
    assert response.status_code == 302
    p.refresh_from_db()
    assert p.altitude_m == 2222
    assert p.canton == "UR"

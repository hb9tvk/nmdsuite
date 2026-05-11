"""Tests for the participant portal log entry (M2.2).

The log is **permissive**: anything the operator types is saved verbatim,
including invalid values. The per-row validity properties on QsoEntry drive
the red ``invalid-cell`` class in the table; the final M2.5 submit is what
enforces every-row-must-be-valid.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from core.models import Participant, QsoEntry
from portal.qso_validators import is_text_payload_valid, is_valid_rst, is_valid_utc, mode_from_rsts

User = get_user_model()


# --- pure validators ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0600", True), ("0959", True), ("0700", True),
        ("0559", False), ("1000", False), ("9999", False), ("abcd", False),
        ("", False), ("600", False),
    ],
)
def test_is_valid_utc(value, expected):
    assert is_valid_utc(value) is expected


@pytest.mark.parametrize(
    "value,expected",
    [("59", True), ("599", True), ("9", False), ("5999", False), ("ab", False), ("", False)],
)
def test_is_valid_rst(value, expected):
    assert is_valid_rst(value) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", True),
        ("a" * 14, False),
        ("a" * 15, True),
        ("hello world today", True),
        ("hello world toda", False),
        ("invalid!chars!here1234", False),
        ("alpha bravo 12345", True),
        ("UPPERCASE works fine", True),
    ],
)
def test_is_text_payload_valid(text, expected):
    assert is_text_payload_valid(text) is expected


@pytest.mark.parametrize("rsts,mode", [("59", "SSB"), ("599", "CW"), ("57", "SSB"), ("339", "CW")])
def test_mode_from_rsts(rsts, mode):
    assert mode_from_rsts(rsts) == mode


# --- view-level CRUD ----------------------------------------------------------------------------


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


VALID_QSO_POST = {
    "editing_id": "",
    "utc": "0612",
    "remote_call": "HB9ABO/P",
    "rsts": "599",
    "txts": "tragtaschenhandel",
    "rstr": "599",
    "txtr": "bitte abstand halten",
}


@pytest.mark.django_db
def test_log_entry_requires_login(client):
    response = client.get("/submission/log/")
    assert response.status_code in (301, 302)


@pytest.mark.django_db
def test_log_entry_redirects_unregistered_user_to_dashboard(client, seeded_contest):
    other = User.objects.create_user(username="HB9XYZ", password="x")
    client.force_login(other)
    response = client.get("/submission/log/")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")


@pytest.mark.django_db
def test_log_entry_renders_form_and_table(client, participant):
    user, p = participant
    client.force_login(user)
    response = client.get("/submission/log/")
    assert response.status_code == 200
    assert b'id="qso-form"' in response.content
    assert b'id="qso-list"' in response.content
    assert b"No QSOs yet" in response.content


@pytest.mark.django_db
def test_qso_save_creates_row_and_returns_full_app(client, participant):
    user, p = participant
    client.force_login(user)
    response = client.post("/submission/log/save/", VALID_QSO_POST)
    assert response.status_code == 200

    qso = QsoEntry.objects.get(participant=p)
    assert qso.utc_raw == "0612"
    assert qso.utc_time is not None
    assert qso.remote_call == "HB9ABO/P"
    assert qso.rsts == "599"
    assert qso.mode == "CW"

    # Response carries the whole #qso-app block (form + table + new row).
    assert b'id="qso-app"' in response.content
    assert b'id="qso-form"' in response.content
    assert f"qso-row-{qso.pk}".encode() in response.content


@pytest.mark.django_db
def test_qso_save_persists_invalid_data_verbatim(client, participant):
    """Permissive save: bad UTC and bad text still go to the database."""
    user, p = participant
    client.force_login(user)
    bad = {**VALID_QSO_POST, "utc": "1234", "txts": "too short"}
    response = client.post("/submission/log/save/", bad)
    assert response.status_code == 200

    qso = QsoEntry.objects.get(participant=p)
    assert qso.utc_raw == "1234"
    assert qso.utc_time is None
    assert qso.txts == "too short"
    assert qso.is_utc_valid is False
    assert qso.is_txts_valid is False


@pytest.mark.django_db
def test_qso_save_normalizes_3digit_utc(client, participant):
    user, p = participant
    client.force_login(user)
    payload = {**VALID_QSO_POST, "utc": "950"}
    client.post("/submission/log/save/", payload)
    qso = QsoEntry.objects.get(participant=p)
    assert qso.utc_raw == "0950"
    assert qso.utc_time is not None


@pytest.mark.django_db
def test_qso_save_with_2digit_rst_persists_as_ssb(client, participant):
    user, p = participant
    client.force_login(user)
    payload = {**VALID_QSO_POST, "rsts": "59", "rstr": "59"}
    client.post("/submission/log/save/", payload)
    assert QsoEntry.objects.get(participant=p).mode == "SSB"


@pytest.mark.django_db
def test_qso_save_empty_form_is_noop(client, participant):
    user, p = participant
    client.force_login(user)
    payload = {k: "" for k in VALID_QSO_POST}
    response = client.post("/submission/log/save/", payload)
    assert response.status_code == 200
    assert QsoEntry.objects.filter(participant=p).count() == 0


@pytest.mark.django_db
def test_qso_save_with_editing_id_updates_existing(client, participant):
    user, p = participant
    client.force_login(user)
    qso = QsoEntry.objects.create(
        participant=p, utc_raw="0700", utc_time=p.contest.start_utc, mode="CW",
        remote_call="HB9ABO/P", rsts="599", rstr="599",
    )
    response = client.post(
        "/submission/log/save/",
        {**VALID_QSO_POST, "editing_id": str(qso.pk), "remote_call": "HB9ZZZ/P", "utc": "0815"},
    )
    assert response.status_code == 200
    qso.refresh_from_db()
    assert qso.remote_call == "HB9ZZZ/P"
    assert qso.utc_raw == "0815"
    assert QsoEntry.objects.filter(participant=p).count() == 1


@pytest.mark.django_db
def test_qso_edit_returns_prefilled_form(client, participant):
    user, p = participant
    client.force_login(user)
    qso = QsoEntry.objects.create(
        participant=p, utc_raw="0612", utc_time=p.contest.start_utc, mode="CW",
        remote_call="HB9ABO/P", rsts="599", rstr="599", txts="tragtaschenhandel",
    )
    response = client.get(f"/submission/log/{qso.pk}/edit/")
    assert response.status_code == 200
    assert b'id="qso-form"' in response.content
    assert b"HB9ABO/P" in response.content
    assert b'value="0612"' in response.content
    assert f'value="{qso.pk}"'.encode() in response.content


@pytest.mark.django_db
def test_qso_delete_removes_row_and_returns_full_app(client, participant):
    user, p = participant
    client.force_login(user)
    qso = QsoEntry.objects.create(
        participant=p, utc_raw="0612", utc_time=p.contest.start_utc, mode="CW",
        remote_call="HB9ABO/P", rsts="599", rstr="599",
    )
    response = client.post(f"/submission/log/{qso.pk}/delete/")
    assert response.status_code == 200
    assert not QsoEntry.objects.filter(pk=qso.pk).exists()
    # Response is the full #qso-app block (form + empty table).
    assert b'id="qso-app"' in response.content
    assert f"qso-row-{qso.pk}".encode() not in response.content


@pytest.mark.django_db
def test_qso_endpoints_reject_other_users_qsos(client, participant, seeded_contest):
    user, p = participant
    other = User.objects.create_user(username="HB9XXX", password="x")
    other_p = Participant.objects.create(
        contest=seeded_contest, user=other, callsign="HB9XXX/P", first_name="X",
        email="x@example.org", coord_system_input="wgs84",
        coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2, ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", operating_modes=3,
    )
    qso = QsoEntry.objects.create(
        participant=other_p, utc_raw="0612", utc_time=seeded_contest.start_utc, mode="CW",
        remote_call="HB9ABO/P", rsts="599", rstr="599",
    )
    client.force_login(user)
    response = client.get(f"/submission/log/{qso.pk}/edit/")
    assert response.status_code == 404
    response = client.post(f"/submission/log/{qso.pk}/delete/")
    assert response.status_code == 404
    assert QsoEntry.objects.filter(pk=qso.pk).exists()


# --- validity properties on QsoEntry ------------------------------------------------------------


@pytest.mark.django_db
def test_qso_is_fully_valid(participant):
    user, p = participant
    qso = QsoEntry.objects.create(
        participant=p, utc_raw="0612", utc_time=p.contest.start_utc, mode="CW",
        remote_call="HB9ABO/P", rsts="599", rstr="599",
        txts="tragtaschenhandel", txtr="bitte abstand halten",
    )
    assert qso.is_fully_valid is True


@pytest.mark.django_db
def test_qso_invalid_fields_flag_correctly(participant):
    user, p = participant
    qso = QsoEntry.objects.create(
        participant=p, utc_raw="9999", remote_call="not-a-call",
        rsts="abc", rstr="59", txts="too short",
    )
    assert qso.is_utc_valid is False
    assert qso.is_remote_call_valid is False
    assert qso.is_rsts_valid is False
    # rstr is 2 digits → individually valid …
    assert qso.is_rstr_valid is True
    # … but the pair lengths disagree
    assert qso.is_rst_pair_consistent is False
    assert qso.is_txts_valid is False
    assert qso.is_fully_valid is False

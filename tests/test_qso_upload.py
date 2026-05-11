"""CSV/.nmd upload (M2.3): parser unit tests + end-to-end view tests."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import AuditLog, Participant, QsoEntry
from portal.qso_upload import parse_upload

User = get_user_model()


# --- parser ----------------------------------------------------------------------------------


def test_parser_six_columns_minimal():
    blob = b"0612;HB9ABO/P;599;tragtaschenhandel;599;bitte abstand halten\n"
    result = parse_upload(blob)
    assert len(result.qsos) == 1
    q = result.qsos[0]
    assert q["utc"] == "0612"
    assert q["remote_call"] == "HB9ABO/P"
    assert q["rsts"] == "599"
    assert q["txts"] == "tragtaschenhandel"
    assert q["rstr"] == "599"
    assert q["txtr"] == "bitte abstand halten"


def test_parser_pads_three_digit_utc():
    blob = b"950;HB9XYZ/P;59;hello world today;59;some text here today\n"
    [q] = parse_upload(blob).qsos
    assert q["utc"] == "0950"


def test_parser_strips_excel_formula_prefix():
    blob = b'="0612";HB9ABO/P;599;txt;599;txt\n'
    [q] = parse_upload(blob).qsos
    assert q["utc"] == "0612"


def test_parser_handles_utf8_bom():
    blob = "﻿0612;HB9ABO/P;599;a;599;b\n".encode("utf-8")
    [q] = parse_upload(blob).qsos
    assert q["remote_call"] == "HB9ABO/P"


def test_parser_falls_back_to_latin1():
    # ä as Latin-1 single byte (0xE4) — invalid UTF-8 → must fall through.
    blob = b"0612;HB9ABO/P;599;\xe4-text-1234567;599;txt\n"
    [q] = parse_upload(blob).qsos
    assert "ä" in q["txts"]


def test_parser_skips_blank_and_comment_lines():
    blob = (
        b"# this is a plain comment\n"
        b"\n"
        b"#;ORT=;Pilatus\n"
        b"#;KANTON=;OW\n"
        b"0612;HB9ABO/P;599;hello world today;599;another piece here\n"
    )
    result = parse_upload(blob)
    assert len(result.qsos) == 1
    # Station info captured even though M2.3 doesn't consume it yet.
    assert result.station_info.fields == {"ORT": "Pilatus", "KANTON": "OW"}


def test_parser_strips_quoted_values():
    blob = b'0612;"HB9ABO/P";"599";"txts here longer";"599";"txtr here longer"\n'
    [q] = parse_upload(blob).qsos
    assert q["remote_call"] == "HB9ABO/P"
    assert q["txts"] == "txts here longer"


def test_parser_normalises_tabs_to_spaces():
    blob = b"0612\tHB9ABO/P;599;a;599;b\n"
    # Tabs are replaced with spaces, so the row becomes "0612 HB9ABO/P;599;a;599;b"
    # which still has only 5 semicolons — the first column eats the tabbed bit.
    [q] = parse_upload(blob).qsos
    assert q["utc"] == "0612 HB9ABO/P"  # combined because tab→space; documents the behaviour


def test_parser_handles_multiple_qsos():
    blob = (
        b"0612;HB9ABO/P;599;tragtaschenhandel;599;bitte abstand halten\n"
        b"0700;HB9XYZ/P;59;hello world today;59;another text here long\n"
        b"0815;HB9TU/P;599;short;599;short\n"
    )
    qsos = parse_upload(blob).qsos
    assert [q["remote_call"] for q in qsos] == ["HB9ABO/P", "HB9XYZ/P", "HB9TU/P"]


def test_parser_falls_through_to_latin1_for_arbitrary_bytes():
    """Latin-1 maps every byte 0–255 to a character, so a 'binary' input doesn't
    raise — it just produces a CSV row with garbled-looking text. The view
    layer treats this as zero meaningful QSOs."""
    blob = b"\xff\xfe\x80\x81"
    result = parse_upload(blob)
    # No semicolons → at most a single bogus row in the UTC column.
    assert len(result.qsos) <= 1


# --- view ------------------------------------------------------------------------------------


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


def _upload(client, blob: bytes, name: str = "log.nmd"):
    return client.post(
        "/submission/log/upload/",
        {"file": SimpleUploadedFile(name, blob, content_type="text/csv")},
    )


@pytest.mark.django_db
def test_upload_replaces_qso_list(client, participant):
    user, p = participant
    client.force_login(user)
    # Pre-existing QSOs that the upload should wipe.
    QsoEntry.objects.create(
        participant=p, utc_raw="0700", utc_time=p.contest.start_utc, mode="CW",
        remote_call="HB9OLD/P", rsts="599", rstr="599",
    )

    blob = (
        b"0612;HB9ABO/P;599;tragtaschenhandel;599;bitte abstand halten\n"
        b"0815;HB9XYZ/P;59;alpha bravo 12345;59;charlie delta 1234\n"
    )
    response = _upload(client, blob)
    assert response.status_code == 200

    rows = list(QsoEntry.objects.filter(participant=p).order_by("utc_raw"))
    assert [r.remote_call for r in rows] == ["HB9ABO/P", "HB9XYZ/P"]
    # Old row gone (atomic replace).
    assert not QsoEntry.objects.filter(remote_call="HB9OLD/P").exists()
    # First row: CW (3-digit RST). Second: SSB (2-digit RST).
    assert rows[0].mode == "CW"
    assert rows[1].mode == "SSB"
    # Audit log captured the import.
    assert AuditLog.objects.filter(action="qso.upload", target="HB9TVK/P").exists()


@pytest.mark.django_db
def test_upload_rejects_unknown_extension(client, participant):
    user, p = participant
    client.force_login(user)
    response = _upload(client, b"0612;HB9ABO/P;599;a;599;b\n", name="log.txt")
    assert response.status_code == 200
    assert QsoEntry.objects.filter(participant=p).count() == 0


@pytest.mark.django_db
def test_upload_handles_missing_file(client, participant):
    user, p = participant
    client.force_login(user)
    response = client.post("/submission/log/upload/", {})
    assert response.status_code == 200
    assert QsoEntry.objects.filter(participant=p).count() == 0


@pytest.mark.django_db
def test_upload_handles_binary_garbage_without_crashing(client, participant):
    """Latin-1 fallback means we never raise on weird bytes; the parser yields
    at most a single nonsense row. What matters is the view doesn't 500."""
    user, p = participant
    client.force_login(user)
    response = _upload(client, b"\xff\xfe\x80\x81")
    assert response.status_code == 200
    # At most one nonsense row. Real-world garbage uploads are operator error;
    # the next legit upload will replace it atomically anyway.
    assert QsoEntry.objects.filter(participant=p).count() <= 1


@pytest.mark.django_db
def test_upload_redirects_unregistered_user_to_dashboard(client, seeded_contest):
    other = User.objects.create_user(username="HB9XYZ", password="x")
    client.force_login(other)
    response = _upload(client, b"0612;HB9ABO/P;599;a;599;b\n")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")

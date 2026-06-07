"""Participant report + picture upload (F3.1)."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from core.models import (
    Contest,
    Participant,
    ParticipantPicture,
    ParticipantReport,
)
from portal import report_service

User = get_user_model()


# --- fixtures --------------------------------------------------------------------------------


@pytest.fixture
def participant(seeded_contest, settings, tmp_path):
    """A registered, non-cancelled participant with NMD_DATA_ROOT
    redirected to a tmp dir so picture uploads don't escape the test
    sandbox."""
    settings.NMD_DATA_ROOT = tmp_path
    user = User.objects.create_user(
        username="HB9TVK", password="x", email="t@example.org", first_name="Peter",
    )
    p = Participant.objects.create(
        contest=seeded_contest, user=user, callsign="HB9TVK",
        first_name="Peter", email="t@example.org",
        coord_system_input="wgs84", coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2,
        ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", operating_modes=3,
    )
    return user, p, tmp_path


def _png_bytes() -> bytes:
    """Minimal 1x1 PNG."""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


# --- service: text ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_save_text_creates_report_lazily(participant):
    _, p, _ = participant
    assert not ParticipantReport.objects.filter(participant=p).exists()
    report_service.save_text(p, "First time saving.")
    assert ParticipantReport.objects.get(participant=p).text == "First time saving."


@pytest.mark.django_db
def test_save_text_truncates_to_4096_chars(participant):
    _, p, _ = participant
    report_service.save_text(p, "x" * 5000)
    assert len(ParticipantReport.objects.get(participant=p).text) == 4096


# --- service: picture upload ----------------------------------------------------------------


@pytest.mark.django_db
def test_upload_writes_file_to_expected_path(participant):
    _, p, tmp_path = participant
    upload = SimpleUploadedFile("vacation.png", _png_bytes(), content_type="image/png")
    picture = report_service.add_picture(p, upload)

    assert picture.idx == 1
    assert picture.extension == "png"
    assert picture.original_filename == "vacation.png"
    expected = (
        tmp_path / str(p.contest.year) / "HB9TVK" / "HB9TVK_1.png"
    )
    assert expected.is_file()
    assert expected.read_bytes() == _png_bytes()


@pytest.mark.django_db
def test_upload_fills_next_empty_slot(participant):
    _, p, _ = participant
    for expected in (1, 2, 3):
        picture = report_service.add_picture(
            p, SimpleUploadedFile(f"x.png", _png_bytes(), content_type="image/png"),
        )
        assert picture.idx == expected


@pytest.mark.django_db
def test_upload_rejects_oversize_image(participant):
    _, p, _ = participant
    too_big = SimpleUploadedFile(
        "huge.jpg", b"x" * (6 * 1024 * 1024), content_type="image/jpeg",
    )
    with pytest.raises(report_service.PictureUploadError):
        report_service.add_picture(p, too_big)
    assert not ParticipantPicture.objects.filter(participant=p).exists()


@pytest.mark.django_db
def test_upload_rejects_unsupported_content_type(participant):
    _, p, _ = participant
    pdf = SimpleUploadedFile("evil.pdf", b"%PDF-...", content_type="application/pdf")
    with pytest.raises(report_service.PictureUploadError):
        report_service.add_picture(p, pdf)


@pytest.mark.django_db
def test_upload_rejected_when_all_slots_full(participant):
    _, p, _ = participant
    for _i in range(6):
        report_service.add_picture(
            p, SimpleUploadedFile("x.png", _png_bytes(), content_type="image/png"),
        )
    with pytest.raises(report_service.PictureUploadError):
        report_service.add_picture(
            p, SimpleUploadedFile("seventh.png", _png_bytes(), content_type="image/png"),
        )


# --- service: delete + cancel ---------------------------------------------------------------


@pytest.mark.django_db
def test_delete_removes_row_and_file(participant):
    _, p, tmp_path = participant
    report_service.add_picture(
        p, SimpleUploadedFile("x.png", _png_bytes(), content_type="image/png"),
    )
    target = tmp_path / str(p.contest.year) / "HB9TVK" / "HB9TVK_1.png"
    assert target.is_file()

    assert report_service.delete_picture(p, 1) is True
    assert not ParticipantPicture.objects.filter(participant=p).exists()
    assert not target.is_file()


@pytest.mark.django_db
def test_delete_returns_false_for_empty_slot(participant):
    _, p, _ = participant
    assert report_service.delete_picture(p, 3) is False


@pytest.mark.django_db
def test_cancel_drops_report_text_and_pictures(participant):
    _, p, tmp_path = participant
    report_service.save_text(p, "I'm leaving.")
    report_service.add_picture(
        p, SimpleUploadedFile("x.png", _png_bytes(), content_type="image/png"),
    )
    expected = tmp_path / str(p.contest.year) / "HB9TVK" / "HB9TVK_1.png"
    assert expected.is_file()

    from registration.services import cancel_participation
    cancel_participation(p)

    p.refresh_from_db()
    assert p.cancelled_at is not None
    assert not ParticipantReport.objects.filter(participant=p).exists()
    assert not ParticipantPicture.objects.filter(participant=p).exists()
    assert not expected.is_file()


# --- portal views ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_portal_report_requires_login(client):
    response = client.get("/submission/report/")
    assert response.status_code in (301, 302)


@pytest.mark.django_db
def test_portal_report_get_renders_page(client, participant):
    user, p, _ = participant
    client.force_login(user)
    response = client.get("/submission/report/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "Report and Pictures" in body or "Teilnehmerbericht" in body


@pytest.mark.django_db
def test_portal_report_save_text(client, participant):
    user, p, _ = participant
    client.force_login(user)
    response = client.post("/submission/report/", {"text": "Sunny day on the Niesen."})
    assert response.status_code == 302
    assert ParticipantReport.objects.get(participant=p).text == "Sunny day on the Niesen."


@pytest.mark.django_db
def test_portal_picture_upload_creates_picture(client, participant):
    user, p, _ = participant
    client.force_login(user)
    upload = SimpleUploadedFile("a.png", _png_bytes(), content_type="image/png")
    response = client.post("/submission/report/picture/upload/", {"picture": upload})
    assert response.status_code == 302
    assert ParticipantPicture.objects.filter(participant=p, idx=1).exists()


@pytest.mark.django_db
def test_portal_picture_delete(client, participant):
    user, p, _ = participant
    client.force_login(user)
    report_service.add_picture(
        p, SimpleUploadedFile("a.png", _png_bytes(), content_type="image/png"),
    )
    response = client.post("/submission/report/picture/1/delete/")
    assert response.status_code == 302
    assert not ParticipantPicture.objects.filter(participant=p).exists()


@pytest.mark.django_db
def test_portal_picture_image_streams_bytes_to_owner(client, participant):
    user, p, _ = participant
    client.force_login(user)
    report_service.add_picture(
        p, SimpleUploadedFile("a.png", _png_bytes(), content_type="image/png"),
    )
    response = client.get("/submission/report/picture/1/image")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response.content == _png_bytes()


@pytest.mark.django_db
def test_portal_picture_image_404_for_non_owner(client, participant, seeded_contest):
    user, p, _ = participant
    report_service.add_picture(
        p, SimpleUploadedFile("a.png", _png_bytes(), content_type="image/png"),
    )
    other = User.objects.create_user(username="HB9XYZ", password="x", email="o@example.org")
    Participant.objects.create(
        contest=seeded_contest, user=other, callsign="HB9XYZ",
        first_name="Other", email="o@example.org",
        coord_system_input="wgs84", coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2, ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", operating_modes=3,
    )
    client.force_login(other)
    response = client.get("/submission/report/picture/1/image")
    assert response.status_code == 404


@pytest.mark.django_db
def test_dashboard_links_to_report_page(client, participant):
    user, _, _ = participant
    client.force_login(user)
    body = client.get("/submission/").content.decode()
    assert "/submission/report/" in body


# --- admin view (F3.2) -----------------------------------------------------------------------


@pytest.mark.django_db
def test_admin_reports_index_requires_staff(client, participant):
    user, _, _ = participant  # non-staff
    client.force_login(user)
    response = client.get("/admin/reports/")
    assert response.status_code in (301, 302, 403)


@pytest.mark.django_db
def test_admin_reports_index_lists_participants_with_content(client, participant):
    user, p, _ = participant
    report_service.save_text(p, "Beautiful day on the mountain.")
    report_service.add_picture(
        p, SimpleUploadedFile("a.png", _png_bytes(), content_type="image/png"),
    )
    staff = User.objects.create_user(
        username="STAFF", password="x", email="s@x.org", is_staff=True,
    )
    client.force_login(staff)
    response = client.get("/admin/reports/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "HB9TVK" in body
    assert "Beautiful day on the mountain." in body
    # Picture link points at the admin image stream, not the portal one.
    assert f"/admin/reports/{p.pk}/picture/1/" in body


@pytest.mark.django_db
def test_admin_reports_index_skips_empty_participants(
    client, participant, seeded_contest,
):
    """A participant with no report text AND no pictures shouldn't show
    up — keeps the magazine-team view focused on actual content."""
    user, p, _ = participant  # never saves anything
    staff = User.objects.create_user(
        username="STAFF", password="x", email="s@x.org", is_staff=True,
    )
    client.force_login(staff)
    body = client.get("/admin/reports/").content.decode()
    assert "HB9TVK" not in body
    assert "No reports submitted yet." in body or "Bisher keine" in body


@pytest.mark.django_db
def test_admin_picture_image_streams_to_staff(client, participant):
    _, p, _ = participant
    report_service.add_picture(
        p, SimpleUploadedFile("a.png", _png_bytes(), content_type="image/png"),
    )
    staff = User.objects.create_user(
        username="STAFF", password="x", email="s@x.org", is_staff=True,
    )
    client.force_login(staff)
    response = client.get(f"/admin/reports/{p.pk}/picture/1/")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert response.content == _png_bytes()


@pytest.mark.django_db
def test_admin_picture_image_blocked_for_non_staff(client, participant):
    user, p, _ = participant
    report_service.add_picture(
        p, SimpleUploadedFile("a.png", _png_bytes(), content_type="image/png"),
    )
    client.force_login(user)  # the owner, but not staff
    response = client.get(f"/admin/reports/{p.pk}/picture/1/")
    assert response.status_code in (301, 302, 403)

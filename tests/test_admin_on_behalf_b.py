"""Administration module — M4.3b on-behalf log, station, and submit/release.

Covers:
- station_service.save_station / apply_upload_station_info — actor override
- qso_service.replace_qsos_from_upload — actor override
- submit_service.submit_log — actor override (no email when on-behalf)
- submit_service.release_log — un-submit per participant, clears auto_submitted
- Admin views under /admin/participants/<pk>/station|log|submit|release/.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from core.models import AuditLog, Participant, QsoEntry
from portal import qso_service, station_service, submit_service

User = get_user_model()


def _make_staff_user(username: str = "STAFF") -> User:
    return User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org", is_staff=True,
    )


def _make_participant(contest, *, username, callsign, submitted=False, auto_submitted=False) -> Participant:
    user = User.objects.create_user(username=username, password="x", email=f"{username.lower()}@x.org")
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign, first_name=username,
        email=f"{username.lower()}@x.org", coord_system_input="wgs84",
        coord_input_e="8.2", coord_input_n="46.8",
        wgs84_lat=46.8, wgs84_lon=8.2, ch1903p_e=2_600_000, ch1903p_n=1_200_000,
        altitude_m=1500, canton="BE", location_text="Niederhorn", operating_modes=3,
        submitted_at=timezone.now() if submitted else None,
        auto_submitted=auto_submitted,
    )


# The unified station-data form inherits every registration field. Tests
# that POST to it need the full payload; this helper keeps the noise
# out of each test body.
_VALID_STATION_FORM = {
    "multi_op": "False",
    "station_chief": "",
    "location_text": "Niederhorn",
    "coord_input_e": "8.2",
    "coord_input_n": "46.8",
    "altitude_m": "1500",
    "canton": "BE",
    "mode_cw": "on",
    "mode_ssb": "on",
    "remarks": "",
}


# --- service: save_station ---------------------------------------------------------------------


@pytest.mark.django_db
def test_save_station_default_actor_is_participant_user(seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    station_service.save_station(participant=p, data={"op_name": "Op", "watt": "5"})
    entry = AuditLog.objects.get(action="station.update", target=p.callsign)
    assert entry.actor == p.user
    assert "on_behalf" not in entry.payload


@pytest.mark.django_db
def test_save_station_with_staff_actor_records_on_behalf(seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    staff = _make_staff_user()
    station_service.save_station(participant=p, data={"op_name": "Op"}, actor=staff)
    entry = AuditLog.objects.get(action="station.update", target=p.callsign)
    assert entry.actor == staff
    assert entry.payload.get("on_behalf") is True


# --- service: replace_qsos_from_upload --------------------------------------------------------


@pytest.mark.django_db
def test_replace_qsos_default_actor_is_participant_user(seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    qso_service.replace_qsos_from_upload(participant=p, rows=[], filename="test.nmd")
    entry = AuditLog.objects.get(action="qso.upload", target=p.callsign)
    assert entry.actor == p.user
    assert "on_behalf" not in entry.payload


@pytest.mark.django_db
def test_replace_qsos_with_staff_actor_records_on_behalf(seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    staff = _make_staff_user()
    qso_service.replace_qsos_from_upload(
        participant=p, rows=[], filename="test.nmd", actor=staff,
    )
    entry = AuditLog.objects.get(action="qso.upload", target=p.callsign)
    assert entry.actor == staff
    assert entry.payload.get("on_behalf") is True


# --- service: submit_log + release_log -------------------------------------------------------


@pytest.mark.django_db
def test_submit_log_default_sends_email(seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    submit_service.submit_log(participant=p)
    p.refresh_from_db()
    assert p.submitted_at is not None
    assert len(mail.outbox) == 1
    entry = AuditLog.objects.get(action="log.submit", target=p.callsign)
    assert entry.actor == p.user
    assert "on_behalf" not in entry.payload


@pytest.mark.django_db
def test_submit_log_on_behalf_skips_email_and_flags_payload(seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    staff = _make_staff_user()
    submit_service.submit_log(participant=p, actor=staff)
    p.refresh_from_db()
    assert p.submitted_at is not None
    # No confirmation email — operator did not trigger this.
    assert mail.outbox == []
    entry = AuditLog.objects.get(action="log.submit", target=p.callsign)
    assert entry.actor == staff
    assert entry.payload.get("on_behalf") is True


@pytest.mark.django_db
def test_release_log_clears_submitted_and_audits(seeded_contest):
    p = _make_participant(
        seeded_contest, username="HB9XYZ", callsign="HB9XYZ",
        submitted=True, auto_submitted=True,
    )
    staff = _make_staff_user()
    submit_service.release_log(participant=p, actor=staff)
    p.refresh_from_db()
    assert p.submitted_at is None
    # auto_submitted also cleared so M4.2's bulk revert won't re-touch this row.
    assert p.auto_submitted is False
    entry = AuditLog.objects.get(action="log.release", target=p.callsign)
    assert entry.actor == staff
    assert entry.payload.get("on_behalf") is True


@pytest.mark.django_db
def test_release_log_noop_when_not_submitted(seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    staff = _make_staff_user()
    submit_service.release_log(participant=p, actor=staff)
    # No row created when there's nothing to release.
    assert not AuditLog.objects.filter(action="log.release").exists()


# --- access control --------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("path", [
    "/admin/participants/{pk}/station/",
    "/admin/participants/{pk}/log/",
])
def test_admin_log_station_redirect_non_staff(client, seeded_contest, path):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    user = User.objects.create_user(username="JOE", password="x", email="j@x.org")
    client.force_login(user)
    response = client.get(path.format(pk=p.pk))
    assert response.status_code in (302, 403)


# --- station view ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_station_get_renders(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    client.force_login(_make_staff_user())
    response = client.get(f"/admin/participants/{p.pk}/station/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "Station data" in body
    assert p.callsign in body


@pytest.mark.django_db
def test_station_post_saves_and_audits(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    staff = _make_staff_user()
    client.force_login(staff)
    form = {
        **_VALID_STATION_FORM,
        "op_name": "Peter",
        "watt": "5",
        "sta01bez": "TX",
        "sta01gramm": "800",
    }
    response = client.post(f"/admin/participants/{p.pk}/station/", form)
    assert response.status_code == 302

    p.refresh_from_db()
    assert p.op_name == "Peter"
    assert p.total_weight_g == 800

    entry = AuditLog.objects.get(action="station.update", target=p.callsign)
    assert entry.actor == staff
    assert entry.payload.get("on_behalf") is True


@pytest.mark.django_db
def test_station_post_bypasses_submitted_lock(client, seeded_contest):
    p = _make_participant(
        seeded_contest, username="HB9XYZ", callsign="HB9XYZ", submitted=True,
    )
    client.force_login(_make_staff_user())
    response = client.post(
        f"/admin/participants/{p.pk}/station/",
        {**_VALID_STATION_FORM, "op_name": "Edited After Submit"},
    )
    assert response.status_code == 302
    p.refresh_from_db()
    assert p.op_name == "Edited After Submit"


# --- log entry view --------------------------------------------------------------------------


@pytest.mark.django_db
def test_log_entry_get_renders(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    client.force_login(_make_staff_user())
    response = client.get(f"/admin/participants/{p.pk}/log/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "HB9XYZ" in body
    # Form section is present and points at the admin save endpoint.
    assert f"/admin/participants/{p.pk}/log/save/" in body


@pytest.mark.django_db
def test_qso_save_creates_qso_and_returns_fragment(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    client.force_login(_make_staff_user())
    response = client.post(
        f"/admin/participants/{p.pk}/log/save/",
        {"utc": "0612", "remote_call": "HB9ABC/P", "rsts": "599", "txts": "x", "rstr": "599", "txtr": "y"},
    )
    assert response.status_code == 200
    assert QsoEntry.objects.filter(participant=p, remote_call="HB9ABC/P").exists()
    body = response.content.decode()
    assert 'id="qso-app"' in body


@pytest.mark.django_db
def test_qso_save_bypasses_submitted_lock(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ", submitted=True)
    client.force_login(_make_staff_user())
    response = client.post(
        f"/admin/participants/{p.pk}/log/save/",
        {"utc": "0612", "remote_call": "HB9ABC/P", "rsts": "599", "txts": "x", "rstr": "599", "txtr": "y"},
    )
    assert response.status_code == 200
    assert QsoEntry.objects.filter(participant=p, remote_call="HB9ABC/P").exists()


@pytest.mark.django_db
def test_qso_edit_returns_form_prefilled(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    qso = QsoEntry.objects.create(
        participant=p, utc_raw="0612", mode="CW",
        remote_call="HB9ABC/P", rsts="599", rstr="599",
    )
    client.force_login(_make_staff_user())
    response = client.get(f"/admin/participants/{p.pk}/log/{qso.pk}/edit/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "HB9ABC/P" in body


@pytest.mark.django_db
def test_qso_delete_removes_row(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    qso = QsoEntry.objects.create(
        participant=p, utc_raw="0612", mode="CW",
        remote_call="HB9ABC/P", rsts="599", rstr="599",
    )
    client.force_login(_make_staff_user())
    response = client.post(f"/admin/participants/{p.pk}/log/{qso.pk}/delete/")
    assert response.status_code == 200
    assert not QsoEntry.objects.filter(pk=qso.pk).exists()


@pytest.mark.django_db
def test_qso_edit_404_for_other_participants_qso(client, seeded_contest):
    """A QSO belongs to a specific participant; admin URLs must enforce
    that pairing so the path can't be spoofed."""
    p1 = _make_participant(seeded_contest, username="HB9AAA", callsign="HB9AAA")
    p2 = _make_participant(seeded_contest, username="HB9BBB", callsign="HB9BBB")
    qso = QsoEntry.objects.create(participant=p2, utc_raw="0612")
    client.force_login(_make_staff_user())
    response = client.get(f"/admin/participants/{p1.pk}/log/{qso.pk}/edit/")
    assert response.status_code == 404


# --- log upload ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_qso_upload_replaces_log_and_audits(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    QsoEntry.objects.create(
        participant=p, utc_raw="0700", utc_time=p.contest.start_utc, mode="CW",
        remote_call="HB9OLD/P", rsts="599", rstr="599",
    )
    staff = _make_staff_user()
    client.force_login(staff)
    response = client.post(
        f"/admin/participants/{p.pk}/log/upload/",
        {"file": SimpleUploadedFile("log.nmd", b"0612;HB9NEW/P;599;a;599;b\n", content_type="text/csv")},
    )
    assert response.status_code == 302
    assert not QsoEntry.objects.filter(participant=p, remote_call="HB9OLD/P").exists()
    assert QsoEntry.objects.filter(participant=p, remote_call="HB9NEW/P").exists()

    entry = AuditLog.objects.get(action="qso.upload", target=p.callsign)
    assert entry.actor == staff
    assert entry.payload.get("on_behalf") is True


@pytest.mark.django_db
def test_qso_upload_bypasses_submitted_lock(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ", submitted=True)
    client.force_login(_make_staff_user())
    response = client.post(
        f"/admin/participants/{p.pk}/log/upload/",
        {"file": SimpleUploadedFile("log.nmd", b"0612;HB9NEW/P;599;a;599;b\n", content_type="text/csv")},
    )
    assert response.status_code == 302
    assert QsoEntry.objects.filter(participant=p, remote_call="HB9NEW/P").exists()


# --- submit / release ------------------------------------------------------------------------


@pytest.mark.django_db
def test_submit_view_submits_and_skips_email(client, seeded_contest, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    staff = _make_staff_user()
    client.force_login(staff)
    response = client.post(f"/admin/participants/{p.pk}/submit/")
    assert response.status_code == 302
    p.refresh_from_db()
    assert p.submitted_at is not None
    assert mail.outbox == []  # no operator email on on-behalf submit
    entry = AuditLog.objects.get(action="log.submit", target=p.callsign)
    assert entry.actor == staff
    assert entry.payload.get("on_behalf") is True


@pytest.mark.django_db
def test_submit_view_get_is_forbidden(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    client.force_login(_make_staff_user())
    response = client.get(f"/admin/participants/{p.pk}/submit/")
    assert response.status_code == 405


@pytest.mark.django_db
def test_release_view_unsubmits(client, seeded_contest):
    p = _make_participant(
        seeded_contest, username="HB9XYZ", callsign="HB9XYZ",
        submitted=True, auto_submitted=True,
    )
    staff = _make_staff_user()
    client.force_login(staff)
    response = client.post(f"/admin/participants/{p.pk}/release/")
    assert response.status_code == 302
    p.refresh_from_db()
    assert p.submitted_at is None
    assert p.auto_submitted is False
    entry = AuditLog.objects.get(action="log.release", target=p.callsign)
    assert entry.actor == staff


@pytest.mark.django_db
def test_release_view_idempotent_when_not_submitted(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    client.force_login(_make_staff_user())
    response = client.post(f"/admin/participants/{p.pk}/release/")
    assert response.status_code == 302
    p.refresh_from_db()
    assert p.submitted_at is None
    # No audit row written for a no-op release.
    assert not AuditLog.objects.filter(action="log.release").exists()


# --- detail page shows the right action button ------------------------------------------------


@pytest.mark.django_db
def test_detail_page_shows_submit_when_pending(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ")
    client.force_login(_make_staff_user())
    body = client.get(f"/admin/participants/{p.pk}/").content.decode()
    assert "Submit log on behalf" in body
    assert "Release submission" not in body


@pytest.mark.django_db
def test_detail_page_shows_release_when_submitted(client, seeded_contest):
    p = _make_participant(seeded_contest, username="HB9XYZ", callsign="HB9XYZ", submitted=True)
    client.force_login(_make_staff_user())
    body = client.get(f"/admin/participants/{p.pk}/").content.decode()
    assert "Release submission" in body
    assert "Submit log on behalf" not in body

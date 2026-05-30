"""Submit-log (M2.5) — confirm page, lock action, email, and editing-locked behavior."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone

from core.models import AuditLog, EmailLog, Participant, QsoEntry
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
        altitude_m=1500, canton="BE", location_text="Niederhorn", operating_modes=3,
    )
    return user, p


def _force_submitted(p):
    p.submitted_at = timezone.now()
    p.save(update_fields=["submitted_at"])


def _make_submittable(p):
    """Give ``p`` the minimum data required to pass submission validation:
    a logged QSO + the three required components + output power + weight."""
    from core.models import StationComponent
    QsoEntry.objects.create(
        participant=p, utc_raw="0700", utc_time=p.contest.start_utc, mode="CW",
        remote_call="HB9X/P", rsts="599", rstr="599",
        txts="text exchange that is long enough",
        txtr="reply exchange long enough too",
    )
    p.watt = "5W"
    p.total_weight_g = 4500
    p.save(update_fields=["watt", "total_weight_g"])
    StationComponent.objects.create(participant=p, idx=1, description="FT-857", weight_g=1500)
    StationComponent.objects.create(participant=p, idx=2, description="LiFePO4", weight_g=1200)
    StationComponent.objects.create(participant=p, idx=5, description="Linked dipole", weight_g=500)
    return p


# --- confirm page ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_submit_get_renders_confirm_page(client, participant):
    user, p = participant
    _make_submittable(p)
    client.force_login(user)
    response = client.get("/submission/submit/")
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "Confirm and submit" in body
    assert "final" in body.lower()  # "This action is final."


@pytest.mark.django_db
def test_submit_get_shows_invalid_qso_count(client, participant):
    user, p = participant
    _make_submittable(p)
    # Add an invalid QSO on top of the submittable baseline.
    QsoEntry.objects.create(participant=p, utc_raw="bad", remote_call="", rsts="", rstr="")
    client.force_login(user)
    response = client.get("/submission/submit/")
    body = response.content.decode("utf-8")
    # Invalid row is surfaced as a warning but not a blocker.
    assert "invalid fields" in body or "ungültige" in body.lower()
    assert "Confirm and submit" in body  # still submittable


@pytest.mark.django_db
def test_submit_get_shows_weight_over_limit_warning(client, participant):
    user, p = participant
    _make_submittable(p)
    p.total_weight_g = 7500
    p.save(update_fields=["total_weight_g"])
    client.force_login(user)
    response = client.get("/submission/submit/")
    body = response.content.decode("utf-8")
    assert "exceeds the 6 kg" in body


# --- the actual submit action ----------------------------------------------------------------


@pytest.mark.django_db
def test_submit_post_locks_participant_and_sends_email(client, participant, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    user, p = participant
    _make_submittable(p)
    client.force_login(user)
    response = client.post("/submission/submit/")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")

    p.refresh_from_db()
    assert p.submitted_at is not None
    assert AuditLog.objects.filter(action="log.submit", target="HB9TVK/P").exists()
    # Email landed (locmem backend collects it).
    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ["t@example.org"]
    assert "HB9TVK/P" in sent.subject
    # Confirmation body links to the ADIF download in all three languages.
    assert "/submission/log.adi" in sent.body
    assert "ADIF" in sent.body
    assert EmailLog.objects.filter(recipient="t@example.org", status=EmailLog.Status.SENT).exists()


@pytest.mark.django_db
def test_submit_when_already_submitted_redirects_and_keeps_timestamp(client, participant):
    user, p = participant
    _force_submitted(p)
    first_submitted_at = p.submitted_at
    client.force_login(user)
    response = client.post("/submission/submit/")
    assert response.status_code == 302
    p.refresh_from_db()
    # Submit is one-way; timestamp must not be bumped on a repeat call.
    assert p.submitted_at == first_submitted_at


@pytest.mark.django_db
def test_submit_requires_login(client):
    response = client.get("/submission/submit/")
    assert response.status_code in (301, 302)


# --- locked-state enforcement across editing views -------------------------------------------


@pytest.mark.django_db
def test_qso_save_is_blocked_after_submit(client, participant):
    user, p = participant
    _force_submitted(p)
    client.force_login(user)
    response = client.post("/submission/log/save/", {
        "utc": "0612", "remote_call": "HB9ABC/P", "rsts": "599", "txts": "x", "rstr": "599", "txtr": "y",
    })
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")
    # No QSO was created.
    assert QsoEntry.objects.filter(participant=p).count() == 0


@pytest.mark.django_db
def test_qso_upload_is_blocked_after_submit(client, participant):
    from django.core.files.uploadedfile import SimpleUploadedFile

    user, p = participant
    QsoEntry.objects.create(
        participant=p, utc_raw="0700", utc_time=p.contest.start_utc, mode="CW",
        remote_call="HB9OLD/P", rsts="599", rstr="599",
    )
    _force_submitted(p)
    client.force_login(user)
    response = client.post(
        "/submission/log/upload/",
        {"file": SimpleUploadedFile("log.nmd", b"0612;HB9X/P;599;a;599;b\n", content_type="text/csv")},
    )
    assert response.status_code == 302
    # Old QSO untouched — upload was rejected before parsing.
    assert QsoEntry.objects.filter(participant=p, remote_call="HB9OLD/P").exists()
    assert not QsoEntry.objects.filter(participant=p, remote_call="HB9X/P").exists()


@pytest.mark.django_db
def test_station_post_is_blocked_after_submit(client, participant):
    user, p = participant
    _force_submitted(p)
    client.force_login(user)
    response = client.post("/submission/station/", {"watt": "should not save"})
    assert response.status_code == 302
    p.refresh_from_db()
    assert p.watt != "should not save"


@pytest.mark.django_db
def test_cancel_is_blocked_after_submit(client, participant):
    user, p = participant
    _force_submitted(p)
    client.force_login(user)
    response = client.get("/submission/profile/cancel/")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")


# --- read-only views still render -------------------------------------------------------------


@pytest.mark.django_db
def test_log_entry_get_renders_read_only_after_submit(client, participant):
    user, p = participant
    QsoEntry.objects.create(
        participant=p, utc_raw="0612", utc_time=p.contest.start_utc, mode="CW",
        remote_call="HB9ABC/P", rsts="599", rstr="599",
    )
    _force_submitted(p)
    client.force_login(user)
    response = client.get("/submission/log/")
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    # Form is hidden, no Edit/Delete actions per row.
    assert 'id="qso-form-section"' not in body
    assert "Submitted" in body or "submitted" in body
    # The QSO itself is still rendered.
    assert "HB9ABC/P" in body


# --- submission validation -------------------------------------------------------------------


@pytest.mark.django_db
def test_submit_blocked_when_log_is_empty(client, participant):
    """Empty logs cannot be submitted. The confirm page shows the blocker
    and hides the submit button."""
    user, p = participant
    # Equipment is fine, just no QSOs.
    from core.models import StationComponent
    p.watt = "5W"
    p.total_weight_g = 3000
    p.save(update_fields=["watt", "total_weight_g"])
    StationComponent.objects.create(participant=p, idx=1, description="TX")
    StationComponent.objects.create(participant=p, idx=2, description="PSU")
    StationComponent.objects.create(participant=p, idx=5, description="Antenna")

    client.force_login(user)
    body = client.get("/submission/submit/").content.decode()
    assert "log is empty" in body.lower() or "leer" in body.lower()
    assert "Confirm and submit" not in body  # button hidden

    # POST is rejected too.
    response = client.post("/submission/submit/")
    assert response.status_code == 302
    p.refresh_from_db()
    assert p.submitted_at is None


@pytest.mark.django_db
def test_submit_blocked_when_required_equipment_missing(client, participant):
    """TRX / PSU / Antenna / Watt / weight > 0 are all required."""
    user, p = participant
    QsoEntry.objects.create(
        participant=p, utc_raw="0700", utc_time=p.contest.start_utc, mode="CW",
        remote_call="HB9X/P", rsts="599", rstr="599",
    )
    # No equipment at all.
    client.force_login(user)
    body = client.get("/submission/submit/").content.decode()
    assert "Transceiver" in body
    assert "Power supply" in body
    assert "Antenna" in body
    assert "Output power" in body
    assert "weight" in body.lower()
    assert "Confirm and submit" not in body


@pytest.mark.django_db
def test_submit_post_rejected_by_service_if_blocking_state(client, participant):
    """If the operator slips through the form and POSTs while still
    invalid, the service refuses defensively."""
    user, p = participant
    # No QSOs, no equipment — every blocker triggers.
    client.force_login(user)
    response = client.post("/submission/submit/")
    assert response.status_code == 302
    p.refresh_from_db()
    assert p.submitted_at is None
    # Reasons surface as flash messages on the redirect target.
    follow = client.get(response["Location"])
    body = follow.content.decode()
    assert "log is empty" in body.lower() or "leer" in body.lower()


@pytest.mark.django_db
def test_submit_allows_invalid_qso_rows_as_warning(client, participant):
    """Invalid UTC / short text / dupe QSOs are warnings, not blockers —
    the operator can still confirm."""
    user, p = participant
    _make_submittable(p)
    QsoEntry.objects.create(
        participant=p, utc_raw="zzzz", remote_call="", rsts="", rstr="",
    )

    client.force_login(user)
    response = client.post("/submission/submit/")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")
    p.refresh_from_db()
    assert p.submitted_at is not None


@pytest.mark.django_db
def test_dashboard_hides_destructive_actions_after_submit(client, participant):
    user, p = participant
    _force_submitted(p)
    client.force_login(user)
    response = client.get("/submission/")
    body = response.content.decode("utf-8")
    # Submit / Unsubscribe / upload form must be gone post-submit.
    assert "Submit log" not in body
    assert "Unsubscribe" not in body
    assert "qso-upload-form" not in body
    # Read-only views still linked (button stays so the operator can
    # see the saved data, even though edits are blocked).
    assert "Log entry" in body
    assert "Station data" in body

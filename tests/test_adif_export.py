"""ADIF export of the participant's submitted log.

Service-level: build_participant_adif emits a valid ADIF body with
standard fields only, byte-length tags, and skips unparseable rows.

View-level: gated on login + submitted_at, served as a download with
the right filename.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone as djtimezone

from core.adif_export import build_participant_adif
from core.models import Participant, QsoEntry

User = get_user_model()


# --- helpers ---------------------------------------------------------------------------------


def _make_participant(
    contest, *, username="HB9TVK", callsign="HB9TVK/P", first_name="Peter",
    submitted=True, modes=3,
):
    user = User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org",
    )
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign,
        first_name=first_name, email=f"{username.lower()}@x.org",
        coord_system_input="ch1903plus",
        coord_input_e="2681239", coord_input_n="1237065",
        ch1903p_e=2_681_239, ch1903p_n=1_237_065,
        wgs84_lat=46.8, wgs84_lon=8.5,
        altitude_m=1500, canton="ZH", operating_modes=modes,
        submitted_at=djtimezone.now() if submitted else None,
    )


def _add_qso(
    participant, *, utc_str="0700", mode="CW", remote="HB9X/P",
    rsts=None, rstr=None, txts="text exchange", txtr="reply exchange",
    valid=True,
):
    """Create a QsoEntry. ``valid=False`` leaves utc_time/mode unset
    (simulating an un-parseable row that the operator saved as a draft)."""
    if rsts is None:
        rsts = "599" if mode == "CW" else "59"
    if rstr is None:
        rstr = "599" if mode == "CW" else "59"
    contest_date = participant.contest.contest_date
    utc_dt = None
    stored_mode = ""
    if valid:
        utc_dt = datetime.combine(
            contest_date,
            datetime.strptime(utc_str, "%H%M").time(),
            tzinfo=timezone.utc,
        )
        stored_mode = mode
    return QsoEntry.objects.create(
        participant=participant,
        utc_raw=utc_str,
        utc_time=utc_dt,
        mode=stored_mode,
        remote_call=remote,
        rsts=rsts, txts=txts,
        rstr=rstr, txtr=txtr,
    )


def _records(adif_text: str) -> list[str]:
    """Split the body into individual ``<EOR>``-terminated records.

    Header is everything up to and including ``<EOH>``; records follow.
    """
    after_header = adif_text.split("<EOH>", 1)[1]
    parts = [r for r in after_header.split("<EOR>") if r.strip()]
    return [p.strip() for p in parts]


# --- service: header + structure -------------------------------------------------------------


@pytest.mark.django_db
def test_adif_has_header_block(seeded_contest):
    p = _make_participant(seeded_contest)
    text = build_participant_adif(p)
    assert "<EOH>" in text
    assert "<ADIF_VER:5>3.1.4" in text
    assert "<PROGRAMID:8>NMDSuite" in text
    # Header free-text comment carries the operator + year for traceability.
    assert "HB9TVK/P" in text
    assert "NMD 2026" in text


@pytest.mark.django_db
def test_adif_records_use_standard_tags_only(seeded_contest):
    p = _make_participant(seeded_contest)
    _add_qso(p, utc_str="0700", mode="CW", remote="HB9B")
    text = build_participant_adif(p)
    records = _records(text)
    assert len(records) == 1
    rec = records[0]
    # Standard ADIF fields present.
    assert "<CALL:4>HB9B" in rec
    assert f"<QSO_DATE:8>{seeded_contest.contest_date.strftime('%Y%m%d')}" in rec
    assert "<TIME_ON:4>0700" in rec
    assert "<BAND:3>80M" in rec
    assert "<MODE:2>CW" in rec
    assert "<RST_SENT:3>599" in rec
    assert "<RST_RCVD:3>599" in rec
    assert "<CONTEST_ID:8>USKA-NMD" in rec
    assert "<OPERATOR:8>HB9TVK/P" in rec
    assert "<STATION_CALLSIGN:8>HB9TVK/P" in rec
    # And no app-defined scoring annotations (deliberate scope decision).
    assert "APP_NMDSUITE" not in rec


@pytest.mark.django_db
def test_adif_ends_with_trailing_newline(seeded_contest):
    p = _make_participant(seeded_contest)
    text = build_participant_adif(p)
    assert text.endswith("\n")


# --- service: cleaning policy ----------------------------------------------------------------


@pytest.mark.django_db
def test_adif_skips_qsos_without_utc_time(seeded_contest):
    """Draft QSOs (operator typed something invalid, didn't fix it) had
    utc_time left null by the parser. Exporting them would mislead the
    receiving logger so we skip them."""
    p = _make_participant(seeded_contest)
    _add_qso(p, utc_str="0700", mode="CW", remote="HB9OK")
    _add_qso(p, utc_str="bad-", mode="CW", remote="HB9SKIP", valid=False)

    text = build_participant_adif(p)
    assert "HB9OK" in text
    assert "HB9SKIP" not in text


@pytest.mark.django_db
def test_adif_skips_qsos_without_mode(seeded_contest):
    """The scoring engine ignores rows where mode is blank; ADIF
    follows the same policy."""
    p = _make_participant(seeded_contest)
    q = _add_qso(p, utc_str="0700", mode="CW", remote="HB9SKIP")
    q.mode = ""
    q.save(update_fields=["mode"])
    _add_qso(p, utc_str="0710", mode="CW", remote="HB9OK")

    text = build_participant_adif(p)
    assert "HB9OK" in text
    assert "HB9SKIP" not in text


@pytest.mark.django_db
def test_adif_records_ordered_by_utc(seeded_contest):
    p = _make_participant(seeded_contest)
    _add_qso(p, utc_str="0815", mode="SSB", remote="HB9LATE")
    _add_qso(p, utc_str="0700", mode="CW", remote="HB9EARLY")

    text = build_participant_adif(p)
    early = text.find("HB9EARLY")
    late = text.find("HB9LATE")
    assert 0 < early < late


# --- service: byte-length encoding -----------------------------------------------------------


@pytest.mark.django_db
def test_adif_tag_length_is_utf8_byte_count(seeded_contest):
    """ADIF tag length is the byte count of the value (not character
    count). Umlauts encode as 2 bytes in UTF-8 — must be reflected so
    parsers don't truncate or run past the value."""
    p = _make_participant(seeded_contest)
    # 4 ASCII chars + 'ä' (2 bytes UTF-8) = 6 bytes total. The 5-char
    # string 'Bär 1' encodes to 6 bytes.
    _add_qso(p, utc_str="0700", mode="CW", txts="Bär 1", remote="HB9A")

    text = build_participant_adif(p)
    assert "<STX_STRING:6>Bär 1" in text


@pytest.mark.django_db
def test_adif_omits_empty_exchange_text_fields(seeded_contest):
    """If the operator logged no free-form text in either direction we
    drop the STX_STRING / SRX_STRING tags entirely — emitting
    ``<STX_STRING:0>`` is valid ADIF but noisy."""
    p = _make_participant(seeded_contest)
    _add_qso(p, utc_str="0700", mode="CW", remote="HB9A", txts="", txtr="")

    text = build_participant_adif(p)
    assert "STX_STRING" not in text
    assert "SRX_STRING" not in text


@pytest.mark.django_db
def test_adif_empty_log_still_produces_valid_file(seeded_contest):
    """A submitted participant with zero scored QSOs still gets a
    valid (header-only) ADIF file."""
    p = _make_participant(seeded_contest)
    text = build_participant_adif(p)
    assert "<EOH>" in text
    assert _records(text) == []


# --- view: gating + response shape -----------------------------------------------------------


@pytest.mark.django_db
def test_view_requires_login(client, seeded_contest):
    response = client.get("/submission/log.adi")
    assert response.status_code in (301, 302)
    assert "/submission/login/" in response["Location"]


@pytest.mark.django_db
def test_view_redirects_when_not_submitted(client, seeded_contest):
    """An operator who hasn't pressed Submit must not be able to pull a
    canonical export — gentle redirect to dashboard, no error."""
    p = _make_participant(seeded_contest, submitted=False)
    client.force_login(p.user)
    response = client.get("/submission/log.adi")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")


@pytest.mark.django_db
def test_view_redirects_when_user_is_not_a_participant(client, seeded_contest):
    """Staff or arbitrary logged-in users who never registered also
    get redirected — there's nothing for them to download here."""
    staff = User.objects.create_user(
        username="STAFF", password="x", email="s@x.org", is_staff=True,
    )
    client.force_login(staff)
    response = client.get("/submission/log.adi")
    assert response.status_code == 302
    assert response["Location"].endswith("/submission/")


@pytest.mark.django_db
def test_view_serves_adif_with_filename_when_submitted(client, seeded_contest):
    p = _make_participant(seeded_contest)
    _add_qso(p, utc_str="0700", mode="CW", remote="HB9A")
    client.force_login(p.user)

    response = client.get("/submission/log.adi")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    cd = response["Content-Disposition"]
    assert "attachment" in cd
    # Filename uses the contest year and a slash-safe form of the callsign.
    assert f"nmd-{seeded_contest.year}-HB9TVK-P.adi" in cd
    # And the body is real ADIF content.
    assert "<EOH>" in response.content.decode("utf-8")
    assert "HB9A" in response.content.decode("utf-8")


# --- dashboard link visibility --------------------------------------------------------------


@pytest.mark.django_db
def test_dashboard_hides_adif_link_before_submission(client, seeded_contest):
    p = _make_participant(seeded_contest, submitted=False)
    client.force_login(p.user)
    body = client.get("/submission/").content.decode()
    assert "/submission/log.adi" not in body


@pytest.mark.django_db
def test_dashboard_shows_adif_link_after_submission(client, seeded_contest):
    p = _make_participant(seeded_contest, submitted=True)
    client.force_login(p.user)
    body = client.get("/submission/").content.decode()
    assert "/submission/log.adi" in body
    assert "ADIF" in body

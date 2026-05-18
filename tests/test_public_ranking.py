"""Public ranking page + station data + participant map (M4A.2).

Service-level tests exercise the ranking aggregation directly.
View-level tests check state gating, public access (no login), and
that the rendered page contains the expected content.
"""
from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import (
    Contest,
    Participant,
    QsoEntry,
    ScoringRecord,
    StationComponent,
    StationDescription,
)
from public.ranking_service import build_ranking_page

User = get_user_model()


# --- helpers ---------------------------------------------------------------------------------


def _make_participant(
    contest, *, username, callsign, first_name=None, modes=3,
    cancelled=False, submitted=True, weight_g=0,
    components=None, watt="", lat=46.8, lon=8.5, altitude_m=1500,
    location_text="",
):
    """Create a Participant in ``contest`` with optional station data.

    ``components`` is a dict mapping slot idx (1-based) to description text.
    By default the participant is submitted (so they appear in rankings)
    and not cancelled.
    """
    user = User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org",
    )
    p = Participant.objects.create(
        contest=contest, user=user, callsign=callsign,
        first_name=first_name or username, email=f"{username.lower()}@x.org",
        coord_system_input="ch1903plus",
        coord_input_e="2681239", coord_input_n="1237065",
        ch1903p_e=2_681_239, ch1903p_n=1_237_065,
        wgs84_lat=lat, wgs84_lon=lon,
        altitude_m=altitude_m, canton="ZH", operating_modes=modes,
        submitted_at=timezone.now() if submitted else None,
        cancelled_at=timezone.now() if cancelled else None,
    )
    if components is not None or watt or weight_g or location_text:
        station = StationDescription.objects.create(
            participant=p, watt=watt, total_weight_g=weight_g,
            location_text=location_text,
        )
        for idx, desc in (components or {}).items():
            StationComponent.objects.create(station=station, idx=idx, description=desc)
    return p


def _add_qso(participant, *, mode, points, remote="HB9X/P", status="full_match"):
    """Create one scored QSO row for the given participant.

    The scoring engine would attach a ScoringRecord; here we shortcut
    that for tests so we don't have to run a full scoring pass.
    """
    qso = QsoEntry.objects.create(
        participant=participant,
        utc_raw="0700",
        utc_time=timezone.now(),
        mode=mode,
        remote_call=remote,
        rsts="59" if mode == "SSB" else "599",
        txts="text " * 4,
        rstr="59" if mode == "SSB" else "599",
        txtr="reply " * 3,
    )
    ScoringRecord.objects.create(qso=qso, status=status, points=points)
    return qso


@pytest.fixture
def published_contest(seeded_contest):
    """Move the seeded contest to PUBLISHED so the public view is reachable."""
    seeded_contest.state = Contest.State.PUBLISHED
    seeded_contest.results_published_at = timezone.now()
    seeded_contest.save(update_fields=["state", "results_published_at"])
    return seeded_contest


# --- service: ranking ------------------------------------------------------------------------


@pytest.mark.django_db
def test_empty_contest_returns_empty_page(published_contest):
    page = build_ranking_page(published_contest)
    assert page.cw == []
    assert page.ssb == []
    assert page.stations == []
    assert page.markers == []


@pytest.mark.django_db
def test_cw_ranking_includes_only_cw_registered_participants(published_contest):
    cw_only = _make_participant(published_contest, username="HB9CW", callsign="HB9CW/P", modes=1)
    ssb_only = _make_participant(published_contest, username="HB9SSB", callsign="HB9SSB/P", modes=2)
    both = _make_participant(published_contest, username="HB9BOTH", callsign="HB9BOTH/P", modes=3)
    _add_qso(cw_only, mode="CW", points=4)
    _add_qso(ssb_only, mode="SSB", points=4)
    _add_qso(both, mode="CW", points=4)
    _add_qso(both, mode="SSB", points=4)

    page = build_ranking_page(published_contest)
    cw_calls = {r.callsign for r in page.cw}
    ssb_calls = {r.callsign for r in page.ssb}
    assert cw_calls == {"HB9CW/P", "HB9BOTH/P"}
    assert ssb_calls == {"HB9SSB/P", "HB9BOTH/P"}


@pytest.mark.django_db
def test_ranking_points_summed_from_scoring_records(published_contest):
    p = _make_participant(published_contest, username="HB9A", callsign="HB9A/P", modes=1)
    _add_qso(p, mode="CW", points=4)
    _add_qso(p, mode="CW", points=4)
    _add_qso(p, mode="CW", points=1)  # non-NMD QSO

    page = build_ranking_page(published_contest)
    assert len(page.cw) == 1
    assert page.cw[0].points == 9


@pytest.mark.django_db
def test_ranking_orders_points_desc_then_weight_asc_then_callsign(published_contest):
    """Three CW operators tied on points except for the tiebreakers."""
    a = _make_participant(
        published_contest, username="HB9A", callsign="HB9A/P", modes=1, weight_g=5000,
    )
    b = _make_participant(
        published_contest, username="HB9B", callsign="HB9B/P", modes=1, weight_g=3000,
    )
    c = _make_participant(
        published_contest, username="HB9C", callsign="HB9C/P", modes=1, weight_g=3000,
    )
    _add_qso(a, mode="CW", points=8)
    _add_qso(b, mode="CW", points=8)
    _add_qso(c, mode="CW", points=8)

    page = build_ranking_page(published_contest)
    # All three have 8 points; b (3000g) and c (3000g) tie on weight,
    # so callsign breaks it: B before C. A's heavier station lands last.
    assert [r.callsign for r in page.cw] == ["HB9B/P", "HB9C/P", "HB9A/P"]
    assert [r.rank for r in page.cw] == [1, 2, 3]


@pytest.mark.django_db
def test_ranking_omits_cancelled_and_unsubmitted(published_contest):
    """Cancelled rows and not-yet-submitted rows must not appear."""
    active = _make_participant(published_contest, username="HB9OK", callsign="HB9OK/P", modes=1)
    _make_participant(published_contest, username="HB9CX", callsign="HB9CX/P", modes=1, cancelled=True)
    _make_participant(published_contest, username="HB9NS", callsign="HB9NS/P", modes=1, submitted=False)
    _add_qso(active, mode="CW", points=4)

    page = build_ranking_page(published_contest)
    assert [r.callsign for r in page.cw] == ["HB9OK/P"]


@pytest.mark.django_db
def test_ranking_includes_zero_point_participants_who_registered_for_the_mode(published_contest):
    """A station that registered for CW but logged no scored QSOs should
    still appear in the CW ranking at the bottom with 0 points."""
    no_score = _make_participant(published_contest, username="HB9Z", callsign="HB9Z/P", modes=1)
    has_score = _make_participant(published_contest, username="HB9A", callsign="HB9A/P", modes=1)
    _add_qso(has_score, mode="CW", points=4)

    page = build_ranking_page(published_contest)
    assert [(r.callsign, r.points) for r in page.cw] == [
        ("HB9A/P", 4),
        ("HB9Z/P", 0),
    ]


# --- service: ranking QSO breakdown ----------------------------------------------------------


@pytest.mark.django_db
def test_ranking_qso_breakdown_by_status(published_contest):
    """The four QSO columns map to specific ScoringStatus categories:

    - NMD column: FULL_MATCH + ADMIN_ACCEPTED (4-point matches)
    - HB column: HB9_QSO (1-point Swiss non-NMD)
    - EU column: DX_QSO (1-point non-Swiss DX)
    - Unscored statuses (TEXT_MISMATCH, UNMATCHED, SUSPECTED, DUPE)
      don't appear in any column.
    """
    p = _make_participant(published_contest, username="HB9A", callsign="HB9A/P", modes=1)
    # 3 NMD matches (= 12 points)
    _add_qso(p, mode="CW", points=4, status="full_match", remote="HB9B")
    _add_qso(p, mode="CW", points=4, status="full_match", remote="HB9C")
    _add_qso(p, mode="CW", points=4, status="admin_accepted", remote="HB9D")
    # 2 HB QSOs (= 2 points)
    _add_qso(p, mode="CW", points=1, status="hb9_qso", remote="HB9E")
    _add_qso(p, mode="CW", points=1, status="hb9_qso", remote="HB9F")
    # 1 EU/DX QSO (= 1 point)
    _add_qso(p, mode="CW", points=1, status="dx_qso", remote="DL1ABC")
    # These should be invisible in all columns:
    _add_qso(p, mode="CW", points=0, status="text_mismatch", remote="HB9G")
    _add_qso(p, mode="CW", points=0, status="unmatched", remote="HB9H")
    _add_qso(p, mode="CW", points=0, status="dupe_deducted", remote="HB9I")

    row = build_ranking_page(published_contest).cw[0]
    assert row.nmd_qsos == 3
    assert row.hb_qsos == 2
    assert row.eu_qsos == 1
    assert row.total_qsos == 6
    assert row.points == 15


@pytest.mark.django_db
def test_ranking_qso_breakdown_partitioned_by_mode(published_contest):
    """CW and SSB QSOs land in their respective mode's ranking only."""
    p = _make_participant(published_contest, username="HB9A", callsign="HB9A/P", modes=3)
    _add_qso(p, mode="CW", points=4, status="full_match", remote="HB9B")
    _add_qso(p, mode="CW", points=1, status="hb9_qso", remote="HB9C")
    _add_qso(p, mode="SSB", points=4, status="full_match", remote="HB9D")

    page = build_ranking_page(published_contest)
    cw_row = page.cw[0]
    ssb_row = page.ssb[0]
    assert (cw_row.nmd_qsos, cw_row.hb_qsos, cw_row.eu_qsos, cw_row.points) == (1, 1, 0, 5)
    assert (ssb_row.nmd_qsos, ssb_row.hb_qsos, ssb_row.eu_qsos, ssb_row.points) == (1, 0, 0, 4)


# --- service: station data -------------------------------------------------------------------


@pytest.mark.django_db
def test_station_data_uses_legacy_slot_mapping(published_contest):
    """TRX = slot 1, PSU = slot 2, Antenna = slot 5. Verifies the
    convention agreed for M4A.2."""
    p = _make_participant(
        published_contest, username="HB9A", callsign="HB9A/P", modes=3,
        watt="100W", weight_g=4200,
        components={
            1: "FT-857",            # TRX
            2: "LiFePO4 12V",       # PSU
            5: "Linked dipole",     # Antenna
            6: "RG-174 10m",        # Feedline — should NOT be on station data
        },
    )
    _add_qso(p, mode="CW", points=8)

    page = build_ranking_page(published_contest)
    assert len(page.stations) == 1
    s = page.stations[0]
    assert s.callsign == "HB9A/P"
    assert s.trx == "FT-857"
    assert s.psu == "LiFePO4 12V"
    assert s.antenna == "Linked dipole"
    assert s.watt == "100W"
    assert s.total_weight_g == 4200


@pytest.mark.django_db
def test_station_data_total_is_cw_plus_ssb(published_contest):
    p = _make_participant(published_contest, username="HB9A", callsign="HB9A/P", modes=3)
    _add_qso(p, mode="CW", points=4)
    _add_qso(p, mode="CW", points=4)
    _add_qso(p, mode="SSB", points=4)

    page = build_ranking_page(published_contest)
    assert page.stations[0].points_total == 12


@pytest.mark.django_db
def test_station_data_sorted_by_combined_points(published_contest):
    a = _make_participant(published_contest, username="HB9A", callsign="HB9A/P", modes=3)
    b = _make_participant(published_contest, username="HB9B", callsign="HB9B/P", modes=3)
    _add_qso(a, mode="CW", points=4)
    _add_qso(b, mode="CW", points=4)
    _add_qso(b, mode="SSB", points=8)  # b wins combined total

    page = build_ranking_page(published_contest)
    assert [s.callsign for s in page.stations] == ["HB9B/P", "HB9A/P"]


@pytest.mark.django_db
def test_station_data_handles_missing_station_description(published_contest):
    """Participant who submitted a log but never filled the station form."""
    p = _make_participant(published_contest, username="HB9A", callsign="HB9A/P", modes=1)
    _add_qso(p, mode="CW", points=4)

    page = build_ranking_page(published_contest)
    s = page.stations[0]
    assert s.trx == ""
    assert s.watt == ""
    assert s.psu == ""
    assert s.antenna == ""
    assert s.total_weight_g == 0


# --- service: markers ------------------------------------------------------------------------


@pytest.mark.django_db
def test_markers_one_per_submitted_active_participant(published_contest):
    _make_participant(published_contest, username="HB9A", callsign="HB9A/P", modes=1, lat=46.7, lon=8.3)
    _make_participant(published_contest, username="HB9B", callsign="HB9B/P", modes=2, lat=47.0, lon=8.7)
    _make_participant(published_contest, username="HB9CX", callsign="HB9CX/P", modes=1, cancelled=True)

    page = build_ranking_page(published_contest)
    calls = {m.callsign for m in page.markers}
    assert calls == {"HB9A/P", "HB9B/P"}


# --- view: state gating ----------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("blocking_state", [
    Contest.State.REGISTRATION_OPEN,
    Contest.State.REGISTRATION_CLOSED,
    Contest.State.LOGS_OPEN,
    Contest.State.LOGS_CLOSED,
    Contest.State.SCORED,
])
def test_view_404s_unless_published_or_archived(client, seeded_contest, blocking_state):
    seeded_contest.state = blocking_state
    seeded_contest.save(update_fields=["state"])
    response = client.get(f"/ranking/{seeded_contest.year}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_view_404_for_nonexistent_year(client, seeded_contest):
    response = client.get("/ranking/1999/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_view_200_for_published(client, published_contest):
    response = client.get(f"/ranking/{published_contest.year}/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_view_200_for_archived(client, seeded_contest):
    seeded_contest.state = Contest.State.ARCHIVED
    seeded_contest.save(update_fields=["state"])
    response = client.get(f"/ranking/{seeded_contest.year}/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_view_does_not_require_login(client, published_contest):
    """Anonymous request must succeed — this is the whole point of the page."""
    assert "_auth_user_id" not in client.session
    response = client.get(f"/ranking/{published_contest.year}/")
    assert response.status_code == 200


# --- view: rendered content -----------------------------------------------------------------


@pytest.mark.django_db
def test_view_renders_callsigns_and_ranking_tables(client, published_contest):
    p = _make_participant(
        published_contest, username="HB9TVK", callsign="HB9TVK/P",
        first_name="Peter", modes=3,
    )
    _add_qso(p, mode="CW", points=8)
    _add_qso(p, mode="SSB", points=4)

    response = client.get(f"/ranking/{published_contest.year}/")
    body = response.content.decode()
    assert "HB9TVK/P" in body
    assert "Peter" in body
    # Both ranking sections present (heading text).
    assert "CW ranking" in body
    assert "SSB ranking" in body
    assert "Station data" in body


@pytest.mark.django_db
def test_view_emits_marker_json_block(client, published_contest):
    p = _make_participant(
        published_contest, username="HB9A", callsign="HB9A/P",
        modes=1, lat=46.85, lon=8.31,
    )
    _add_qso(p, mode="CW", points=4)

    response = client.get(f"/ranking/{published_contest.year}/")
    body = response.content.decode()
    # json_script puts the payload in a typed script tag with a known id.
    assert '<script id="ranking-markers" type="application/json">' in body
    assert "HB9A/P" in body


@pytest.mark.django_db
def test_view_serves_swisstopo_tile_attribution(client, published_contest):
    """Sanity: the page wires up the swisstopo tile attribution via the
    JS bundle and includes the leaflet stylesheet."""
    response = client.get(f"/ranking/{published_contest.year}/")
    body = response.content.decode()
    assert "leaflet" in body.lower()
    assert "ranking_map.js" in body

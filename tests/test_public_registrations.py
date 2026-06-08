"""Public list of currently-registered stations (next contest)."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import Contest, Participant

User = get_user_model()


def _make_participant(contest, *, username, callsign, modes=3, cancelled=False):
    user = User.objects.create_user(
        username=username, password="x", email=f"{username.lower()}@x.org",
    )
    return Participant.objects.create(
        contest=contest, user=user, callsign=callsign,
        first_name=username, email=f"{username.lower()}@x.org",
        coord_system_input="ch1903plus",
        coord_input_e="2681239", coord_input_n="1237065",
        ch1903p_e=2_681_239, ch1903p_n=1_237_065,
        wgs84_lat=46.8, wgs84_lon=8.5,
        altitude_m=1500, canton="BE", operating_modes=modes,
        location_text="Niesen",
        cancelled_at=timezone.now() if cancelled else None,
    )


@pytest.mark.django_db
def test_public_registrations_anonymous_access_lists_active_participants(client, seeded_contest):
    """No login required, the table includes every non-cancelled participant
    of the upcoming contest with the expected columns."""
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A", modes=1)
    _make_participant(seeded_contest, username="HB9B", callsign="HB9B", modes=2)
    _make_participant(seeded_contest, username="HB9C", callsign="HB9C", modes=3)
    _make_participant(seeded_contest, username="HB9X", callsign="HB9X", cancelled=True)

    response = client.get("/ranking/registrations/")
    assert response.status_code == 200
    body = response.content.decode()
    # Every active row appears.
    assert "HB9A" in body
    assert "HB9B" in body
    assert "HB9C" in body
    # Cancelled rows do not.
    assert "HB9X" not in body
    # Mode column derives from operating_modes.
    assert "CW+SSB" in body
    # CH1903 coords use the LV03 properties with slash separator.
    assert "681239/237065" in body


@pytest.mark.django_db
def test_public_registrations_sorted_by_callsign(client, seeded_contest):
    _make_participant(seeded_contest, username="HB9ZZZ", callsign="HB9ZZZ")
    _make_participant(seeded_contest, username="HB9AAA", callsign="HB9AAA")
    body = client.get("/ranking/registrations/").content.decode()
    assert body.find("HB9AAA") < body.find("HB9ZZZ")


@pytest.mark.django_db
def test_public_registrations_skips_published_archived_contests(client, seeded_contest):
    """Once a contest's results are published / archived, it's no longer
    'upcoming'; the page should fall back to the no-contest message."""
    seeded_contest.state = Contest.State.PUBLISHED
    seeded_contest.results_published_at = timezone.now()
    seeded_contest.save(update_fields=["state", "results_published_at"])
    _make_participant(seeded_contest, username="HB9A", callsign="HB9A")

    body = client.get("/ranking/registrations/").content.decode()
    assert "HB9A" not in body
    # Generic "no upcoming contest" copy surfaces in any of the three locales.
    assert (
        "no upcoming contest" in body.lower()
        or "kein contest" in body.lower()
        or "aucun contest" in body.lower()
        or "nessun contest" in body.lower()
    )


@pytest.mark.django_db
def test_public_registrations_supports_embed_mode(client, seeded_contest):
    """?embed=1 should strip the Django chrome the same way the other
    public surfaces do — the WP iframe relies on it."""
    body = client.get("/ranking/registrations/?embed=1").content.decode()
    assert "embed-mode" in body
    assert "<footer" not in body
"""Label placement for the participant map.

Pure geometry — no database, no rendering. The properties that matter for
a readable map: labels don't collide, they stay on the page, and the same
registrations always produce the same layout.
"""
from __future__ import annotations

import math

import pytest

from core.label_placement import (
    Rect,
    Station,
    place_labels,
)

BOUNDS = Rect(0, 0, 800, 600)


def _station(index: int, x: float, y: float, *, width: float = 40, height: float = 8) -> Station:
    return Station(
        index=index, x=x, y=y, radius=4, label_width=width, label_height=height,
    )


def _pairs(items):
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            yield items[i], items[j]


# --- degenerate input ------------------------------------------------------------------------


def test_no_stations_places_nothing():
    assert place_labels([], BOUNDS) == []


def test_single_station_takes_the_preferred_slot():
    """One station on an empty page should get the cheapest candidate:
    to the east, shortest leader, on the circle's centre line."""
    s = _station(0, 400, 300)
    (placement,) = place_labels([s], BOUNDS)

    assert not placement.overlapping
    # East of the circle, vertically centred on it.
    assert placement.rect.x > s.x
    assert placement.rect.y + placement.rect.height / 2 == pytest.approx(s.y)


# --- the core property: nothing collides -----------------------------------------------------


def test_labels_do_not_overlap_each_other_in_a_cluster():
    """Six stations packed into a small area — the Mittelland case. Every
    label must still find a clear slot."""
    stations = [
        _station(0, 300, 300),
        _station(1, 312, 304),
        _station(2, 305, 292),
        _station(3, 320, 296),
        _station(4, 296, 310),
        _station(5, 330, 308),
    ]
    placements = place_labels(stations, BOUNDS)

    for a, b in _pairs(placements):
        assert not a.rect.overlaps(b.rect), (
            f"labels {a.station_index} and {b.station_index} collide"
        )


def test_labels_do_not_cover_other_stations_circles():
    stations = [_station(i, 200 + i * 25, 300) for i in range(6)]
    placements = place_labels(stations, BOUNDS)

    for p in placements:
        for s in stations:
            if s.index == p.station_index:
                continue
            # Closest point on the label rect to the circle centre.
            nx = max(p.rect.x, min(s.x, p.rect.right))
            ny = max(p.rect.y, min(s.y, p.rect.top))
            distance_sq = (s.x - nx) ** 2 + (s.y - ny) ** 2
            assert distance_sq >= s.radius**2, (
                f"label {p.station_index} covers circle {s.index}"
            )


def test_two_stations_at_the_same_spot_still_both_get_labels():
    """Degenerate but possible: a multi-op and a single-op registering the
    same summit. Neither label may be dropped."""
    stations = [_station(0, 400, 300), _station(1, 400, 300)]
    placements = place_labels(stations, BOUNDS)

    assert len(placements) == 2
    assert not placements[0].rect.overlaps(placements[1].rect)


# --- staying on the page ---------------------------------------------------------------------


def test_labels_stay_inside_bounds():
    """Stations hard against every edge, including the corners."""
    stations = [
        _station(0, 10, 10),
        _station(1, 790, 590),
        _station(2, 10, 590),
        _station(3, 790, 10),
        _station(4, 400, 5),
    ]
    placements = place_labels(stations, BOUNDS)

    for p in placements:
        assert p.rect.x >= BOUNDS.x
        assert p.rect.y >= BOUNDS.y
        assert p.rect.right <= BOUNDS.right
        assert p.rect.top <= BOUNDS.top


def test_station_near_the_right_edge_labels_westward():
    """There is no room to the east, so the label has to flip sides rather
    than run off the page."""
    s = _station(0, 780, 300)
    (placement,) = place_labels([s], BOUNDS)

    assert placement.rect.right <= BOUNDS.right
    assert placement.rect.right < s.x  # west of the circle


# --- leader lines ----------------------------------------------------------------------------


def test_leader_connects_circle_edge_to_label_edge():
    s = _station(0, 400, 300)
    (p,) = place_labels([s], BOUNDS)

    from_x, from_y = p.leader_from
    to_x, to_y = p.leader_to
    # Starts on the circle's edge, at its centre height.
    assert abs(from_x - s.x) == pytest.approx(s.radius)
    assert from_y == pytest.approx(s.y)
    # Ends on the label's near edge, at the label's centre height.
    assert to_y == pytest.approx(p.rect.y + p.rect.height / 2)
    assert to_x == pytest.approx(p.rect.x) or to_x == pytest.approx(p.rect.right)


def test_leader_points_at_the_circle_centre():
    """A stacked label sits off its circle's centre line. Its leader must
    aim at the centre and stop at the rim; a leader that instead started at
    the circle's horizontal extreme would meet the rim as a tangent and
    read as a line brushing past the circle rather than pointing at it.
    """
    stations = [_station(i, 300 + (i % 3) * 14, 300 + (i // 3) * 10) for i in range(9)]
    placements = place_labels(stations, BOUNDS)
    by_index = {s.index: s for s in stations}

    stacked = 0
    for p in placements:
        s = by_index[p.station_index]
        fx, fy = p.leader_from
        tx, ty = p.leader_to

        # Starts exactly on the rim — no gap, and no covering the outline.
        assert math.dist((fx, fy), (s.x, s.y)) == pytest.approx(s.radius)
        # Centre, rim point and label end are collinear, so extending the
        # leader inward would arrive at the centre.
        cross = (fx - s.x) * (ty - s.y) - (fy - s.y) * (tx - s.x)
        assert cross == pytest.approx(0, abs=1e-6), (
            f"leader {p.station_index} is not aimed at its circle's centre"
        )
        # Aimed outward, not back through the circle.
        assert (fx - s.x) * (tx - s.x) + (fy - s.y) * (ty - s.y) > 0

        if abs(ty - s.y) > 1e-9:
            stacked += 1

    assert stacked, "this cluster was supposed to force some labels off-centre"


# --- reproducibility -------------------------------------------------------------------------


def test_placement_is_deterministic():
    """The same registrations must always produce the same map — otherwise
    the PDF reshuffles itself on every download."""
    stations = [_station(i, 200 + (i % 7) * 18, 250 + (i // 7) * 14) for i in range(21)]

    first = place_labels(stations, BOUNDS)
    second = place_labels(stations, BOUNDS)

    assert [(p.station_index, p.rect, p.leader_to) for p in first] == [
        (p.station_index, p.rect, p.leader_to) for p in second
    ]


def test_placements_are_returned_in_input_order():
    stations = [_station(0, 100, 100), _station(1, 500, 400), _station(2, 300, 200)]
    placements = place_labels(stations, BOUNDS)
    assert [p.station_index for p in placements] == [0, 1, 2]

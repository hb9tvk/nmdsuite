"""Collision-avoiding label placement for the participant map.

The map draws one circle per registered station at its own fixed position,
with the callsign printed beside it and a short leader line connecting the
two. Circles cannot move — they *are* the data. Only the labels can, so
this module answers one question: where does each label go so that it
overlaps neither another label nor any circle?

Pure geometry. No Django, no reportlab — the caller supplies plain numbers
in canvas units and gets plain numbers back, which keeps the hard part
testable without rendering anything.

Decisions encoded here:

- **Candidate set mirrors the legacy map.** The hand-drawn predecessor put
  labels to the left or right of the circle on the same line, and stacked
  them vertically when the area got crowded. We generate exactly that:
  each side, at growing leader lengths, at growing vertical offsets. No
  diagonal placement — it reads worse and the legacy map never needed it.
- **Most-constrained-first.** Stations are placed in order of how many
  neighbours sit within crowding range. A station alone in the Jura will
  find a slot whatever order we use; one inside the Mittelland cluster
  may have exactly one. Placing the crowded ones while the canvas is
  still empty is what makes the difference.
- **Soft costs, never a hard failure.** An impossible input (fifty
  stations on one summit) must still produce a map. Overlap is charged as
  a heavy penalty rather than rejecting the candidate, so the worst case
  degrades into visible overlap instead of a missing label. Only leaving
  the drawable area is a genuine rejection.
- **Deterministic.** No randomness, and every tie is broken by station
  index, so the same registrations always yield the same map. Tests would
  be untrustworthy otherwise, and a map that reshuffles itself on every
  download looks broken to the reader.
- **Refinement pass.** Greedy placement commits early choices before it
  knows what comes later, so afterwards each label gets one chance to move
  somewhere cheaper given the final layout. Iterated until nothing moves
  or the pass budget runs out.
- **Leaders aim at the circle's centre**, clipped at the rim. A stacked
  label sits above or below its circle, and a leader that started at the
  circle's horizontal extreme would meet the rim as a tangent — it reads
  as a line brushing past the circle rather than pointing at it.

Coordinates are y-up (reportlab's convention). Nothing here depends on
that beyond the naming of the vertical offsets, but mixing conventions in
the caller would flip the stacking order.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Gap between the circle's edge and the near edge of the label. Also the
# shortest leader line we will draw.
LEADER_MIN = 2.0
# Each further ring pushes the label this much further out.
LEADER_STEP = 6.0
# How many leader lengths to try per side.
LEADER_RINGS = 4
# Vertical offsets tried at each leader length, as multiples of the label
# height: level, one line up, one line down, two up, two down.
VERTICAL_STEPS = (0, 1, -1, 2, -2)

# Penalties. Overlap dominates everything else by design: a slightly
# longer leader is always preferable to a collision.
COST_LABEL_OVERLAP = 10_000.0
COST_CIRCLE_OVERLAP = 6_000.0
COST_LEADER_CROSSES_CIRCLE = 400.0
COST_PER_LEADER_UNIT = 3.0
COST_PER_VERTICAL_LEVEL = 12.0
# Charged when nothing at all fits inside the drawable area.
COST_OUT_OF_BOUNDS = 20_000.0

# Neighbours within this many canvas units count toward "crowded" when
# deciding placement order.
CROWDING_RADIUS = 60.0

MAX_REFINEMENT_PASSES = 3


@dataclass(frozen=True)
class Station:
    """One circle to be labelled. ``x``/``y`` is the circle centre."""

    index: int
    x: float
    y: float
    radius: float
    label_width: float
    label_height: float


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y + self.height

    def overlaps(self, other: "Rect") -> bool:
        """Axis-aligned overlap. Edge contact is not overlap."""
        return (
            self.x < other.right
            and other.x < self.right
            and self.y < other.top
            and other.y < self.top
        )

    def overlap_area(self, other: "Rect") -> float:
        dx = min(self.right, other.right) - max(self.x, other.x)
        dy = min(self.top, other.top) - max(self.y, other.y)
        if dx <= 0 or dy <= 0:
            return 0.0
        return dx * dy


@dataclass(frozen=True)
class Placement:
    """Where one station's label ended up.

    ``leader_*`` are the endpoints of the connecting line: from the circle's
    rim to the near edge of the label, on the label's centre line. The rim
    end is wherever the line *toward* the label crosses the circle, so the
    leader always points at the centre — see :func:`_rim_point`.

    ``overlapping`` is tracked explicitly rather than inferred from ``cost``
    — enough leader-crossing penalties can reach the overlap threshold on
    their own, and a caller deciding whether the map needs a human eye
    should not have to guess which penalties added up.
    """

    station_index: int
    rect: Rect
    leader_from: tuple[float, float]
    leader_to: tuple[float, float]
    cost: float
    overlapping: bool


def _circle_rect_overlap(cx: float, cy: float, r: float, rect: Rect) -> bool:
    """True if a circle overlaps an axis-aligned rectangle."""
    nearest_x = max(rect.x, min(cx, rect.right))
    nearest_y = max(rect.y, min(cy, rect.top))
    dx = cx - nearest_x
    dy = cy - nearest_y
    return (dx * dx + dy * dy) < (r * r)


def _segment_hits_circle(
    p0: tuple[float, float], p1: tuple[float, float], cx: float, cy: float, r: float
) -> bool:
    """True if the segment ``p0``–``p1`` passes through the given circle.

    Keeps leader lines from being drawn straight across an unrelated
    station's circle. Standard point-to-segment distance.
    """
    x0, y0 = p0
    x1, y1 = p1
    seg_dx, seg_dy = x1 - x0, y1 - y0
    seg_len_sq = seg_dx * seg_dx + seg_dy * seg_dy
    if seg_len_sq == 0:
        dist_sq = (cx - x0) ** 2 + (cy - y0) ** 2
    else:
        t = ((cx - x0) * seg_dx + (cy - y0) * seg_dy) / seg_len_sq
        t = max(0.0, min(1.0, t))
        nx, ny = x0 + t * seg_dx, y0 + t * seg_dy
        dist_sq = (cx - nx) ** 2 + (cy - ny) ** 2
    return dist_sq < (r * r)


# One candidate: where the label would sit, the leader's two endpoints, and
# the cost of the position itself (before any collision is considered).
_Candidate = tuple[Rect, tuple[float, float], tuple[float, float], float]


def _rim_point(station: Station, toward: tuple[float, float]) -> tuple[float, float]:
    """Where the line from the circle's centre to ``toward`` crosses the rim.

    Drawing the leader from here is the same as drawing it from the centre
    and clipping it at the circle, without covering the outline.
    """
    dx = toward[0] - station.x
    dy = toward[1] - station.y
    distance = math.hypot(dx, dy)
    if distance == 0:
        # Degenerate: a label centred exactly on its own circle. Any rim
        # point will do, so keep the historical eastward one.
        return (station.x + station.radius, station.y)
    scale = station.radius / distance
    return (station.x + dx * scale, station.y + dy * scale)


def _candidates(station: Station) -> list[_Candidate]:
    """Every position we are willing to consider for one label.

    Ordered from most to least preferred: short leaders before long ones,
    and on-the-line before stacked. ``base_cost`` charges only for leader
    length and vertical displacement — collisions are scored later, against
    the layout as it stands at placement time.
    """
    out: list[_Candidate] = []
    half_h = station.label_height / 2.0
    for ring in range(LEADER_RINGS):
        leader = LEADER_MIN + ring * LEADER_STEP
        for level in VERTICAL_STEPS:
            centre_y = station.y + level * station.label_height
            rect_y = centre_y - half_h
            base = leader * COST_PER_LEADER_UNIT + abs(level) * COST_PER_VERTICAL_LEVEL
            # East: label sits to the right of the circle.
            east_to = (station.x + station.radius + leader, centre_y)
            out.append((
                Rect(
                    station.x + station.radius + leader,
                    rect_y,
                    station.label_width,
                    station.label_height,
                ),
                _rim_point(station, east_to),
                east_to,
                base,
            ))
            # West: mirrored, so the label's right edge meets the leader.
            to_x = station.x - station.radius - leader
            west_to = (to_x, centre_y)
            out.append((
                Rect(
                    to_x - station.label_width,
                    rect_y,
                    station.label_width,
                    station.label_height,
                ),
                _rim_point(station, west_to),
                west_to,
                base,
            ))
    return out


def _score(
    candidate: _Candidate,
    station: Station,
    stations: list[Station],
    placed: dict[int, Rect],
) -> tuple[float, bool]:
    """Score one candidate against the layout as it currently stands.

    Returns ``(cost, overlapping)`` where ``overlapping`` means the label
    would sit on top of another label or an unrelated circle.
    """
    rect, leader_from, leader_to, cost = candidate
    overlapping = False

    for other_index, other_rect in placed.items():
        if other_index == station.index:
            continue
        if rect.overlaps(other_rect):
            cost += COST_LABEL_OVERLAP + rect.overlap_area(other_rect)
            overlapping = True

    for other in stations:
        if other.index == station.index:
            # A label can never cover its own circle — the leader starts at
            # that circle's edge — and its own leader trivially touches it.
            continue
        if _circle_rect_overlap(other.x, other.y, other.radius, rect):
            cost += COST_CIRCLE_OVERLAP
            overlapping = True
        if _segment_hits_circle(leader_from, leader_to, other.x, other.y, other.radius):
            cost += COST_LEADER_CROSSES_CIRCLE

    return cost, overlapping


def _crowding(station: Station, stations: list[Station]) -> int:
    """How many other circles sit within :data:`CROWDING_RADIUS`."""
    return sum(
        1
        for other in stations
        if other.index != station.index
        and math.dist((station.x, station.y), (other.x, other.y)) <= CROWDING_RADIUS
    )


def _inside(rect: Rect, bounds: Rect) -> bool:
    return (
        rect.x >= bounds.x
        and rect.y >= bounds.y
        and rect.right <= bounds.right
        and rect.top <= bounds.top
    )


def place_labels(stations: list[Station], bounds: Rect) -> list[Placement]:
    """Lay out every station's label inside ``bounds``.

    Returns one :class:`Placement` per station, in the input's order. A
    station whose label fits nowhere inside ``bounds`` still gets a
    placement — its first candidate — so the caller never has to handle a
    missing label; check :attr:`Placement.overlapping` to detect a layout
    that came out dirty.
    """
    if not stations:
        return []

    candidates = {s.index: _candidates(s) for s in stations}
    # Most crowded first; index breaks ties so the order is reproducible.
    order = sorted(stations, key=lambda s: (-_crowding(s, stations), s.index))

    placed: dict[int, Rect] = {}
    chosen: dict[int, Placement] = {}

    def best_for(station: Station) -> Placement:
        best: Placement | None = None
        for candidate in candidates[station.index]:
            rect, l_from, l_to, _base = candidate
            if not _inside(rect, bounds):
                continue
            cost, overlapping = _score(candidate, station, stations, placed)
            if best is None or cost < best.cost:
                best = Placement(station.index, rect, l_from, l_to, cost, overlapping)
        if best is None:
            # Nothing fits inside the drawable area. Fall back to the first
            # candidate so the label is still drawn, and say so in the cost.
            rect, l_from, l_to, base = candidates[station.index][0]
            return Placement(
                station.index, rect, l_from, l_to, base + COST_OUT_OF_BOUNDS, True,
            )
        return best

    for station in order:
        result = best_for(station)
        chosen[station.index] = result
        placed[station.index] = result.rect

    # Greedy commits before it knows what follows, so give everyone another
    # look at the finished layout — repeatedly, until nothing improves.
    for _ in range(MAX_REFINEMENT_PASSES):
        moved = False
        for station in order:
            current = chosen[station.index]
            del placed[station.index]
            result = best_for(station)
            if result.cost < current.cost - 1e-9:
                chosen[station.index] = result
                moved = True
            placed[station.index] = chosen[station.index].rect
        if not moved:
            break

    return [chosen[s.index] for s in stations]

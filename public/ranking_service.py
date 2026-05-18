"""Build the per-year ranking page payload (M4A.2).

One :class:`RankingPage` bundles everything the template needs: the two
mode-specific ranking tables, the station-data table, and the marker
list for the map at the top. Constructed in a single pass so the
template doesn't issue further queries.

Tie-breakers (applied in this order, mirroring the contest rules):

    1. points DESC                      — the actual ranking criterion
    2. station total weight ASC         — official rule tiebreaker
    3. callsign ASC                     — deterministic for display
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Q, Sum
from django.db.models.functions import Coalesce

from core.models import Contest, Participant
from portal.station_service import COMPONENT_LABELS

# Fixed slot indexes documented in portal.station_service (1-based).
# Stable since the legacy app — see the COMPONENT_LABELS tuple.
_TRX_IDX = 1
_PSU_IDX = 2
_ANTENNA_IDX = 5


@dataclass(frozen=True)
class RankingRow:
    rank: int
    callsign: str
    first_name: str
    canton: str
    altitude_m: int
    location_text: str
    points: int
    total_weight_g: int


@dataclass(frozen=True)
class StationDataRow:
    rank: int
    callsign: str
    points_total: int
    trx: str
    watt: str
    psu: str
    antenna: str
    total_weight_g: int


@dataclass(frozen=True)
class MapMarker:
    callsign: str
    first_name: str
    altitude_m: int
    location_text: str
    lat: float
    lon: float


@dataclass(frozen=True)
class RankingPage:
    contest: Contest
    cw: list[RankingRow] = field(default_factory=list)
    ssb: list[RankingRow] = field(default_factory=list)
    stations: list[StationDataRow] = field(default_factory=list)
    markers: list[MapMarker] = field(default_factory=list)


def build_ranking_page(contest: Contest) -> RankingPage:
    """Assemble all data the ranking template needs for ``contest``.

    Only counts participants who submitted and were not cancelled. The
    M4.2 ``close log submission`` action sets ``auto_submitted=True``
    on stragglers, so by the time results are published the list is
    complete.
    """
    qs = (
        Participant.objects
        .filter(
            contest=contest,
            cancelled_at__isnull=True,
            submitted_at__isnull=False,
        )
        .annotate(
            cw_points=Coalesce(
                Sum("qsos__score__points", filter=Q(qsos__mode="CW")),
                0,
            ),
            ssb_points=Coalesce(
                Sum("qsos__score__points", filter=Q(qsos__mode="SSB")),
                0,
            ),
        )
        .select_related("station")
        .prefetch_related("station__components")
    )
    participants = list(qs)

    cw = _ranking_for_mode(
        participants,
        mode_bit=Participant.Mode.CW,
        points_attr="cw_points",
    )
    ssb = _ranking_for_mode(
        participants,
        mode_bit=Participant.Mode.SSB,
        points_attr="ssb_points",
    )
    stations = _station_data(participants)
    markers = _markers(participants)

    return RankingPage(
        contest=contest,
        cw=cw, ssb=ssb,
        stations=stations, markers=markers,
    )


def _ranking_for_mode(
    participants: list[Participant], *, mode_bit: int, points_attr: str,
) -> list[RankingRow]:
    """Filter participants to those who registered for ``mode_bit`` and
    sort by (points DESC, station weight ASC, callsign ASC).

    A station registered for the mode appears in its ranking even with
    0 points (e.g. they showed up but logged nothing valid). A station
    not registered for the mode is omitted entirely.
    """
    eligible = [p for p in participants if p.operating_modes & mode_bit]
    eligible.sort(
        key=lambda p: (
            -getattr(p, points_attr),
            _station_weight(p),
            p.callsign,
        ),
    )
    rows: list[RankingRow] = []
    for i, p in enumerate(eligible, start=1):
        rows.append(RankingRow(
            rank=i,
            callsign=p.callsign,
            first_name=p.first_name,
            canton=p.canton,
            altitude_m=p.altitude_m,
            location_text=_location_text(p),
            points=getattr(p, points_attr),
            total_weight_g=_station_weight(p),
        ))
    return rows


def _station_data(participants: list[Participant]) -> list[StationDataRow]:
    """Sort by combined points DESC; same tiebreakers as the ranking tables."""
    annotated = list(participants)
    annotated.sort(
        key=lambda p: (
            -(p.cw_points + p.ssb_points),
            _station_weight(p),
            p.callsign,
        ),
    )
    rows: list[StationDataRow] = []
    for i, p in enumerate(annotated, start=1):
        comps = _components_by_idx(p)
        rows.append(StationDataRow(
            rank=i,
            callsign=p.callsign,
            points_total=p.cw_points + p.ssb_points,
            trx=comps.get(_TRX_IDX, ""),
            watt=getattr(p.station, "watt", "") if hasattr(p, "station") else "",
            psu=comps.get(_PSU_IDX, ""),
            antenna=comps.get(_ANTENNA_IDX, ""),
            total_weight_g=_station_weight(p),
        ))
    return rows


def _markers(participants: list[Participant]) -> list[MapMarker]:
    out: list[MapMarker] = []
    for p in participants:
        if p.wgs84_lat is None or p.wgs84_lon is None:
            continue
        out.append(MapMarker(
            callsign=p.callsign,
            first_name=p.first_name,
            altitude_m=p.altitude_m,
            location_text=_location_text(p),
            lat=p.wgs84_lat,
            lon=p.wgs84_lon,
        ))
    return out


def _station_weight(p: Participant) -> int:
    """Total station weight in grams; 0 if no station description yet."""
    station = getattr(p, "station", None)
    return station.total_weight_g if station else 0


def _location_text(p: Participant) -> str:
    station = getattr(p, "station", None)
    return station.location_text if station and station.location_text else ""


def _components_by_idx(p: Participant) -> dict[int, str]:
    station = getattr(p, "station", None)
    if station is None:
        return {}
    return {c.idx: c.description for c in station.components.all()}


# Re-exported so the view/template can label the component columns from
# the same source of truth that drives the operator's edit form.
TRX_LABEL = COMPONENT_LABELS[_TRX_IDX - 1]
PSU_LABEL = COMPONENT_LABELS[_PSU_IDX - 1]
ANTENNA_LABEL = COMPONENT_LABELS[_ANTENNA_IDX - 1]

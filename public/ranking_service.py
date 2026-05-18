"""Build the per-year ranking page payload (M4A.2).

One :class:`RankingPage` bundles everything the template needs: the two
mode-specific ranking tables, the station-data table, and the marker
list for the map at the top. Constructed in a single pass so the
template doesn't issue further queries.

Ranking rows surface a QSO breakdown that mirrors the legacy ranking
PDF (RL_CW_SSB):

- ``nmd_qsos``: QSOs that scored as a successful NMD↔NMD match (worth
  4 points each). Maps to ``FULL_MATCH`` + ``ADMIN_ACCEPTED``.
- ``hb_qsos``: 1-point Swiss-non-NMD QSOs (``HB9_QSO``).
- ``eu_qsos``: 1-point non-Swiss QSOs (``DX_QSO`` — our engine doesn't
  separate EU from rest-of-world DX, so this is the catch-all).
- QSOs in unscored states (``UNMATCHED``, ``TEXT_MISMATCH``,
  ``SUSPECTED_CALL_MISMATCH``, ``DUPE_DEDUCTED``) appear in no column,
  matching the legacy report.

Tie-breakers (applied in this order, mirroring the contest rules):

    1. points DESC                      — the actual ranking criterion
    2. station total weight ASC         — official rule tiebreaker
    3. callsign ASC                     — deterministic for display
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import Count, Sum

from core.models import Contest, Participant, ScoringRecord, ScoringStatus
from portal.station_service import COMPONENT_LABELS

# Fixed slot indexes documented in portal.station_service (1-based).
# Stable since the legacy app — see the COMPONENT_LABELS tuple.
_TRX_IDX = 1
_PSU_IDX = 2
_ANTENNA_IDX = 5

_NMD_STATUSES = frozenset({ScoringStatus.FULL_MATCH, ScoringStatus.ADMIN_ACCEPTED})


@dataclass(frozen=True)
class RankingRow:
    rank: int
    callsign: str
    first_name: str
    canton: str
    altitude_m: int
    location_text: str
    nmd_qsos: int
    hb_qsos: int
    eu_qsos: int
    total_qsos: int
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


# Internal per-mode counts dict shape: {"nmd": int, "hb": int, "eu": int, "points": int}.
_EMPTY_MODE_SUMMARY = {"nmd": 0, "hb": 0, "eu": 0, "points": 0}


def build_ranking_page(contest: Contest) -> RankingPage:
    """Assemble all data the ranking template needs for ``contest``.

    Only counts participants who submitted and were not cancelled. The
    M4.2 ``close log submission`` action sets ``auto_submitted=True``
    on stragglers, so by the time results are published the list is
    complete.
    """
    participants = list(
        Participant.objects
        .filter(
            contest=contest,
            cancelled_at__isnull=True,
            submitted_at__isnull=False,
        )
        .select_related("station")
        .prefetch_related("station__components")
        .order_by("callsign")
    )
    scoring = _scoring_summary(contest)

    cw = _ranking_for_mode(
        participants, scoring,
        mode_bit=Participant.Mode.CW, mode_str="CW",
    )
    ssb = _ranking_for_mode(
        participants, scoring,
        mode_bit=Participant.Mode.SSB, mode_str="SSB",
    )
    stations = _station_data(participants, scoring)
    markers = _markers(participants)

    return RankingPage(
        contest=contest,
        cw=cw, ssb=ssb,
        stations=stations, markers=markers,
    )


def _scoring_summary(
    contest: Contest,
) -> dict[int, dict[str, dict[str, int]]]:
    """Aggregate scoring records per (participant, mode) into the four
    buckets the ranking row needs.

    One GROUP BY query; we fold statuses into the legacy report
    categories in Python so this stays readable. ``summary[participant_id]
    ['CW']`` returns ``{"nmd": N, "hb": N, "eu": N, "points": P}``.
    """
    rows = (
        ScoringRecord.objects
        .filter(qso__participant__contest=contest)
        .values("qso__participant_id", "qso__mode", "status")
        .annotate(n=Count("id"), pts=Sum("points"))
    )
    summary: dict[int, dict[str, dict[str, int]]] = defaultdict(
        lambda: {"CW": dict(_EMPTY_MODE_SUMMARY), "SSB": dict(_EMPTY_MODE_SUMMARY)},
    )
    for row in rows:
        pid = row["qso__participant_id"]
        mode = row["qso__mode"]
        if mode not in ("CW", "SSB"):
            continue  # QSOs with unparseable mode don't score
        bucket = summary[pid][mode]
        bucket["points"] += row["pts"] or 0
        status = row["status"]
        n = row["n"]
        if status in _NMD_STATUSES:
            bucket["nmd"] += n
        elif status == ScoringStatus.HB9_QSO:
            bucket["hb"] += n
        elif status == ScoringStatus.DX_QSO:
            bucket["eu"] += n
        # Other statuses (UNMATCHED, TEXT_MISMATCH, SUSPECTED_*,
        # DUPE_DEDUCTED) are intentionally not counted — they earn 0
        # points and don't appear on the legacy report.
    return summary


def _ranking_for_mode(
    participants: list[Participant],
    scoring: dict[int, dict[str, dict[str, int]]],
    *,
    mode_bit: int, mode_str: str,
) -> list[RankingRow]:
    """Filter participants to those who registered for ``mode_bit`` and
    sort by (points DESC, station weight ASC, callsign ASC).

    A station registered for the mode appears in its ranking even with
    0 points (e.g. they showed up but logged nothing valid). A station
    not registered for the mode is omitted entirely.
    """
    eligible = [p for p in participants if p.operating_modes & mode_bit]

    def per_mode(p: Participant) -> dict[str, int]:
        return scoring.get(p.id, {}).get(mode_str, dict(_EMPTY_MODE_SUMMARY))

    eligible.sort(
        key=lambda p: (
            -per_mode(p)["points"],
            _station_weight(p),
            p.callsign,
        ),
    )
    rows: list[RankingRow] = []
    for i, p in enumerate(eligible, start=1):
        m = per_mode(p)
        rows.append(RankingRow(
            rank=i,
            callsign=p.callsign,
            first_name=p.first_name,
            canton=p.canton,
            altitude_m=p.altitude_m,
            location_text=_location_text(p),
            nmd_qsos=m["nmd"],
            hb_qsos=m["hb"],
            eu_qsos=m["eu"],
            total_qsos=m["nmd"] + m["hb"] + m["eu"],
            points=m["points"],
            total_weight_g=_station_weight(p),
        ))
    return rows


def _station_data(
    participants: list[Participant],
    scoring: dict[int, dict[str, dict[str, int]]],
) -> list[StationDataRow]:
    """Sort by combined CW+SSB points DESC; same tiebreakers as the ranking tables."""
    def total_points(p: Participant) -> int:
        per = scoring.get(p.id, {})
        return per.get("CW", _EMPTY_MODE_SUMMARY)["points"] + per.get("SSB", _EMPTY_MODE_SUMMARY)["points"]

    annotated = list(participants)
    annotated.sort(
        key=lambda p: (
            -total_points(p),
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
            points_total=total_points(p),
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

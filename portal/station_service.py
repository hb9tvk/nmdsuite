"""Persistence layer for the participant station description.

The station data is a 1:1 ``StationDescription`` (header fields) plus an
ordered child list of ``StationComponent`` rows. The 11 slots have fixed
semantic positions inherited from the legacy nmdlogsubmission app, so
``COMPONENT_LABELS[i-1]`` is the kind of thing slot ``i`` represents
(Transceiver, Stromversorgung, …). Rows the operator left blank are
skipped at save time so the DB never carries empty placeholders.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from core.audit import audit
from core.models import Participant, StationComponent, StationDescription
from registration import swisstopo
from registration.coords import CoordinateError, parse_coordinate_pair

# Fixed slot labels — same order and meaning as the legacy app. Position is
# semantic: STA01 is always the transceiver, STA02 always the power supply,
# etc. Changing the order would scramble previously-saved data.
COMPONENT_LABELS: tuple[str, ...] = (
    _("Transceiver"),
    _("Power supply"),
    _("Headphones / speaker"),
    _("Key / paddle / microphone"),
    _("Antenna / matching unit"),
    _("Feedline"),
    _("Masts / counterweights"),
    _("Guying / insulators"),
    _("PC and accessories"),
    _("Additional station component"),
    _("Additional station component"),
)
COMPONENT_SLOTS = len(COMPONENT_LABELS)


def get_or_init_station(participant: Participant) -> StationDescription:
    station, _created = StationDescription.objects.get_or_create(participant=participant)
    return station


def list_components(station: StationDescription) -> list[StationComponent]:
    return list(station.components.all().order_by("idx"))


def initial_from_station(station: StationDescription) -> dict[str, Any]:
    """Map a station + its components to the form's initial dict."""
    data: dict[str, Any] = {
        "op_name": station.op_name,
        "location_text": station.location_text,
        "watt": station.watt,
    }
    for c in list_components(station):
        if 1 <= c.idx <= COMPONENT_SLOTS:
            data[f"sta{c.idx:02d}bez"] = c.description
            data[f"sta{c.idx:02d}gramm"] = c.weight_g
    return data


@transaction.atomic
def save_station(*, participant: Participant, data: dict[str, Any]) -> StationDescription:
    """Persist the station header + replace the component rows from form data."""
    station = get_or_init_station(participant)
    station.op_name = (data.get("op_name") or "").strip()
    station.location_text = (data.get("location_text") or "").strip()
    station.watt = (data.get("watt") or "").strip()

    station.components.all().delete()
    total = 0
    new_rows = []
    for i in range(1, COMPONENT_SLOTS + 1):
        bez = (data.get(f"sta{i:02d}bez") or "").strip()
        gramm = data.get(f"sta{i:02d}gramm") or 0
        try:
            gramm = int(gramm)
        except (TypeError, ValueError):
            gramm = 0
        if not bez and gramm == 0:
            continue
        new_rows.append(StationComponent(station=station, idx=i, description=bez, weight_g=gramm))
        total += gramm
    StationComponent.objects.bulk_create(new_rows)

    station.total_weight_g = total
    station.save()

    audit(
        action="station.update",
        actor=participant.user,
        target=participant.callsign,
        contest=participant.contest,
        payload={"total_weight_g": total, "component_count": len(new_rows)},
    )
    return station


@dataclass
class UploadOutcome:
    """What `apply_upload_station_info` actually changed.

    None of the three flags being True means the upload's ``#;FIELD=;value``
    lines were empty or didn't carry anything we accept.
    """
    station: StationDescription | None = None
    location_updated: bool = False
    location_invalid: bool = False  # KOORD_X/Y present but outside Switzerland / unparseable

    def __bool__(self) -> bool:
        return self.station is not None or self.location_updated or self.location_invalid


@transaction.atomic
def apply_upload_station_info(participant: Participant, fields: dict[str, str]) -> UploadOutcome:
    """Persist station data extracted from an .nmd upload's ``#;FIELD=;value`` lines.

    - ``OPNAME`` / ``ORT`` / ``WATT`` / ``STA##BEZ`` / ``STA##GRAMM`` → the
      participant's :class:`StationDescription`.
    - ``KOORD_X`` / ``KOORD_Y`` (when both are present and parse to a
      location in/near Switzerland) → the :class:`Participant`'s coordinate
      columns; altitude and canton are then re-derived from Swisstopo. The
      file's own ``QAH`` and ``KANTON`` are intentionally ignored —
      Swisstopo is the authority once coordinates are accepted.
    """
    outcome = UploadOutcome()
    if not fields:
        return outcome

    _apply_location_from_upload(participant, fields, outcome)

    data: dict[str, Any] = {}
    if "OPNAME" in fields:
        data["op_name"] = fields["OPNAME"]
    if "ORT" in fields:
        data["location_text"] = fields["ORT"]
    if "WATT" in fields:
        data["watt"] = fields["WATT"]

    for i in range(1, COMPONENT_SLOTS + 1):
        bez_key, gramm_key = f"STA{i:02d}BEZ", f"STA{i:02d}GRAMM"
        if bez_key in fields:
            data[f"sta{i:02d}bez"] = fields[bez_key]
        if gramm_key in fields:
            try:
                data[f"sta{i:02d}gramm"] = int(fields[gramm_key])
            except ValueError:
                pass

    if data:
        outcome.station = save_station(participant=participant, data=data)
    return outcome


def _apply_location_from_upload(
    participant: Participant, fields: dict[str, str], outcome: UploadOutcome
) -> None:
    """If the upload carries usable coordinates, move the Participant to them.

    Both KOORD_X *and* KOORD_Y must be present and non-empty; if either
    parses but the pair falls outside Switzerland, we flag
    ``location_invalid`` on the outcome (caller surfaces a message) and
    leave the participant unchanged.
    """
    raw_e = (fields.get("KOORD_X") or "").strip()
    raw_n = (fields.get("KOORD_Y") or "").strip()
    if not raw_e or not raw_n:
        return

    try:
        parsed = parse_coordinate_pair(raw_e, raw_n)
    except CoordinateError:
        outcome.location_invalid = True
        return

    participant.coord_system_input = parsed.detected_system
    participant.coord_input_e = raw_e
    participant.coord_input_n = raw_n
    participant.ch1903p_e = parsed.ch1903p_e
    participant.ch1903p_n = parsed.ch1903p_n
    participant.wgs84_lat = parsed.wgs84_lat
    participant.wgs84_lon = parsed.wgs84_lon

    # Altitude + canton come from Swisstopo, never from the file. A failed
    # lookup leaves the previous value in place — the operator can fix it
    # via the registration-edit page if it really matters.
    new_altitude = swisstopo.lookup_altitude(parsed.ch1903p_e, parsed.ch1903p_n)
    if new_altitude is not None:
        participant.altitude_m = new_altitude
    new_canton = swisstopo.lookup_canton(parsed.ch1903p_e, parsed.ch1903p_n)
    if new_canton is not None:
        participant.canton = new_canton

    participant.save()
    outcome.location_updated = True

    audit(
        action="registration.update",
        actor=participant.user,
        target=participant.callsign,
        contest=participant.contest,
        payload={
            "source": "nmd_upload",
            "canton": participant.canton,
            "altitude_m": participant.altitude_m,
            "wgs84_lat": participant.wgs84_lat,
            "wgs84_lon": participant.wgs84_lon,
        },
    )

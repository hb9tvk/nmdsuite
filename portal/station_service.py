"""Persistence layer for the participant's combined station data.

What used to be split across two forms (registration data + station
description) lives on a single ``Participant`` row now (migration
0007). One save path covers everything the operator can edit:
identity-locked registration fields (multi_op, coords, location,
modes, remarks) plus equipment-side fields (op_name, watt, the 11
component slots).

The 11 ``StationComponent`` slots have fixed semantic positions
inherited from the legacy nmdlogsubmission app, so
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
from core.models import Participant, StationComponent
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


def list_components(participant: Participant) -> list[StationComponent]:
    return list(participant.components.all().order_by("idx"))


def initial_from_participant(participant: Participant) -> dict[str, Any]:
    """Pre-fill the unified station-data form from a Participant row.

    Includes both the equipment-side fields and the editable
    registration fields (the form covers both halves now).
    """
    data: dict[str, Any] = {
        # Equipment side.
        "op_name": participant.op_name,
        "watt": participant.watt,
        # Editable registration fields.
        "multi_op": participant.multi_op,
        "station_chief": participant.station_chief,
        "location_text": participant.location_text,
        # Always prefill with CH1903 (LV03) — that's the canonical display
        # format. The form still accepts WGS84 / LV95 on input, but we
        # don't echo back whatever the operator originally typed.
        "coord_input_e": str(participant.ch1903_e) if participant.ch1903_e is not None else "",
        "coord_input_n": str(participant.ch1903_n) if participant.ch1903_n is not None else "",
        "altitude_m": participant.altitude_m,
        "canton": participant.canton,
        "mode_cw": bool(participant.operating_modes & 1),
        "mode_ssb": bool(participant.operating_modes & 2),
        "remarks": participant.remarks,
    }
    for c in list_components(participant):
        if 1 <= c.idx <= COMPONENT_SLOTS:
            data[f"sta{c.idx:02d}bez"] = c.description
            data[f"sta{c.idx:02d}gramm"] = c.weight_g
    return data


@transaction.atomic
def save_station(
    *, participant: Participant, data: dict[str, Any], actor: Any = None,
) -> Participant:
    """Persist the unified station-data form.

    Applies every editable field — equipment, location, multi-op,
    operating modes, remarks — and replaces the component rows. The
    callsign / first_name / email triplet stays immutable.

    ``actor`` defaults to the participant's own user (portal self-edit).
    When admin staff edit on behalf, the staff user is passed in and an
    ``on_behalf=True`` flag is recorded in the audit payload.
    """
    # --- registration-side fields (only set what's present in `data`
    # so callers that pass partial dicts — e.g. .nmd uploads — don't
    # blank out fields they didn't carry).
    if "multi_op" in data:
        participant.multi_op = bool(data["multi_op"])
    if "station_chief" in data:
        participant.station_chief = (data.get("station_chief") or "").strip()
    if "location_text" in data:
        participant.location_text = (data.get("location_text") or "").strip()
    if "parsed_coords" in data:
        parsed = data["parsed_coords"]
        participant.coord_system_input = parsed.detected_system
        participant.coord_input_e = data.get("coord_input_e", "")
        participant.coord_input_n = data.get("coord_input_n", "")
        participant.ch1903p_e = parsed.ch1903p_e
        participant.ch1903p_n = parsed.ch1903p_n
        participant.wgs84_lat = parsed.wgs84_lat
        participant.wgs84_lon = parsed.wgs84_lon
    if "altitude_m" in data:
        participant.altitude_m = int(data["altitude_m"])
    if "canton" in data:
        participant.canton = data["canton"]
    if "operating_modes" in data:
        participant.operating_modes = int(data["operating_modes"])
    if "remarks" in data:
        participant.remarks = (data.get("remarks") or "").strip()

    # --- equipment-side fields.
    participant.op_name = (data.get("op_name") or "").strip()
    participant.watt = (data.get("watt") or "").strip()

    # --- component rows: replace wholesale; skip blank slots.
    StationComponent.objects.filter(participant=participant).delete()
    total = 0
    new_rows: list[StationComponent] = []
    for i in range(1, COMPONENT_SLOTS + 1):
        bez = (data.get(f"sta{i:02d}bez") or "").strip()
        gramm = data.get(f"sta{i:02d}gramm") or 0
        try:
            gramm = int(gramm)
        except (TypeError, ValueError):
            gramm = 0
        if not bez and gramm == 0:
            continue
        new_rows.append(
            StationComponent(participant=participant, idx=i, description=bez, weight_g=gramm),
        )
        total += gramm
    StationComponent.objects.bulk_create(new_rows)

    participant.total_weight_g = total
    participant.save()

    audit_actor = actor or participant.user
    audit_payload: dict[str, Any] = {
        "total_weight_g": total,
        "component_count": len(new_rows),
        "canton": participant.canton,
        "altitude_m": participant.altitude_m,
    }
    if actor is not None and actor != participant.user:
        audit_payload["on_behalf"] = True
    audit(
        action="station.update",
        actor=audit_actor,
        target=participant.callsign,
        contest=participant.contest,
        payload=audit_payload,
    )
    return participant


@dataclass
class UploadOutcome:
    """What `apply_upload_station_info` actually changed.

    None of the three flags being True means the upload's ``#;FIELD=;value``
    lines were empty or didn't carry anything we accept.
    """
    station_updated: bool = False
    location_updated: bool = False
    location_invalid: bool = False  # KOORD_X/Y present but outside Switzerland / unparseable

    def __bool__(self) -> bool:
        return self.station_updated or self.location_updated or self.location_invalid


@transaction.atomic
def apply_upload_station_info(
    participant: Participant, fields: dict[str, str], *, actor: Any = None,
) -> UploadOutcome:
    """Persist station data extracted from an .nmd upload's ``#;FIELD=;value`` lines.

    - ``OPNAME`` / ``WATT`` / ``STA##BEZ`` / ``STA##GRAMM`` → the
      participant's equipment fields and component slots.
    - ``ORT`` → ``Participant.location_text``.
    - ``KOORD_X`` / ``KOORD_Y`` (when both are present and parse to a
      location in/near Switzerland) → the participant's coordinate
      columns; altitude and canton are then re-derived from Swisstopo.
      The file's own ``QAH`` and ``KANTON`` are intentionally ignored —
      Swisstopo is the authority once coordinates are accepted.

    ``actor`` flows through to the audit rows so admin on-behalf uploads
    are attributed correctly.
    """
    outcome = UploadOutcome()
    if not fields:
        return outcome

    _apply_location_from_upload(participant, fields, outcome, actor=actor)

    if "ORT" in fields:
        participant.location_text = (fields["ORT"] or "").strip()
        participant.save(update_fields=["location_text"])

    data: dict[str, Any] = {}
    if "OPNAME" in fields:
        data["op_name"] = fields["OPNAME"]
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
        save_station(participant=participant, data=data, actor=actor)
        outcome.station_updated = True
    return outcome


def _apply_location_from_upload(
    participant: Participant, fields: dict[str, str], outcome: UploadOutcome, *, actor: Any = None,
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
    # via the station-data form if it really matters.
    new_altitude = swisstopo.lookup_altitude(parsed.ch1903p_e, parsed.ch1903p_n)
    if new_altitude is not None:
        participant.altitude_m = new_altitude
    new_canton = swisstopo.lookup_canton(parsed.ch1903p_e, parsed.ch1903p_n)
    if new_canton is not None:
        participant.canton = new_canton

    participant.save()
    outcome.location_updated = True

    audit_actor = actor or participant.user
    audit_payload: dict[str, Any] = {
        "source": "nmd_upload",
        "canton": participant.canton,
        "altitude_m": participant.altitude_m,
        "wgs84_lat": participant.wgs84_lat,
        "wgs84_lon": participant.wgs84_lon,
    }
    if actor is not None and actor != participant.user:
        audit_payload["on_behalf"] = True
    audit(
        action="registration.update",
        actor=audit_actor,
        target=participant.callsign,
        contest=participant.contest,
        payload=audit_payload,
    )

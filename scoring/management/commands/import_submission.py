"""Bulk-import a legacy Flask ``submission.sqlite3`` for validation.

The Flask log-submission tool (``reference/nmdlogsubmission/``) stored
participant accounts, station descriptions, and QSO logs in a single
SQLite DB. Schema (lowercased table names follow SQLAlchemy's default
for the model classes in ``app.py``)::

    user (id, callsign, password, email)
    station_description (id, callsign, opname, email, ort, kanton,
                         koord_x, koord_y, qah, watt,
                         sta01bez, sta01gramm, ..., sta11bez, sta11gramm,
                         gesamtegewicht, submitted)
    log_entry (id, callsign, utc, remote_call, rsts, txts, rstr, txtr)

Unlike :mod:`scoring.management.commands.import_legacy` (which loads
the TCL scorer's QSO-only schema for engine validation), this importer
populates the full participant/station/log triplet so the ranking
page, station-data table, and participant map can be exercised against
realistic data.

Decisions encoded here:

- **Callsign is the join key.** Legacy IDs are not preserved; we look
  things up by callsign across all three tables.
- **Password is dropped.** Legacy hashes are in a different format and
  carry no security value here. Imported users get an unusable
  password — operator would need to reset before login if the test
  fixture ever needs auth.
- **/P convention.** ``/P`` is preserved if present, appended if
  absent. The bare callsign (no suffix) becomes ``User.username``.
- **submitted=True → submitted_at = now**. Stations marked submitted
  in the legacy DB land in the ranking page; non-submitted stay null
  (filtered out by the ranking service, just like in production).
- **Operating modes inferred from QSOs.** RST length 2 → SSB, 3 → CW
  (the project-wide rule). No QSOs → default ``BOTH``.
- **Coordinates may be either LV03 or LV95.** :func:`parse_coordinate_pair`
  detects the system by magnitude. Outside-of-Switzerland values
  silently fall back to stub coords with a warning.
- **Idempotent.** ``get_or_create`` on User/Participant and
  ``replace_qsos_from_upload`` for the log.

Usage::

    python manage.py seed_contest --year 2025 --force
    python manage.py import_submission --year 2025 /path/to/submission.sqlite3
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import (
    Contest,
    Participant,
    StationComponent,
)
from portal.qso_service import replace_qsos_from_upload
from portal.station_service import COMPONENT_SLOTS
from registration.callsigns import login_username, normalize_callsign
from registration.coords import CoordinateError, parse_coordinate_pair

User = get_user_model()

# Stub canton / altitude / coords used when the legacy row is missing
# or unparseable — required NOT NULL fields on Participant must hold
# *something* so the import doesn't fail. The user can fix these up
# later if needed; for validation work they don't affect scoring.
_STUB_CANTON = "ZH"
_STUB_ALTITUDE_M = 800
_STUB_CH1903P_E = 2_600_000.0
_STUB_CH1903P_N = 1_200_000.0
_STUB_WGS84_LAT = 46.8
_STUB_WGS84_LON = 8.2


class Command(BaseCommand):
    help = (
        "Bulk-import a legacy Flask submission.sqlite3 (participants, "
        "station descriptions, and QSO logs) into a contest year for "
        "ranking-surface validation."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--year", type=int, required=True, help="Target contest year.")
        parser.add_argument("db_path", type=str, help="Path to the submission.sqlite3 file.")
        parser.add_argument(
            "--no-portable-suffix",
            action="store_true",
            help="Do not append /P to Participant.callsign when missing.",
        )

    def handle(self, *args, **opts) -> None:
        year = opts["year"]
        try:
            contest = Contest.objects.get(year=year)
        except Contest.DoesNotExist as exc:
            raise CommandError(
                f"No contest with year={year} — seed it first with "
                f"`python manage.py seed_contest --year {year}`."
            ) from exc

        db_path = Path(opts["db_path"])
        if not db_path.is_file():
            raise CommandError(f"Not a file: {db_path}")

        append_p = not opts["no_portable_suffix"]

        conn = sqlite3.connect(str(db_path))
        try:
            conn.row_factory = sqlite3.Row
            users_by_call = self._load_users(conn)
            stations_by_call = self._load_stations(conn)
            logs_by_call = self._load_logs_grouped(conn)
        finally:
            conn.close()

        all_callsigns = sorted(set(users_by_call) | set(stations_by_call) | set(logs_by_call))
        if not all_callsigns:
            raise CommandError("Legacy DB has no users, stations, or logs.")

        counts = {"participants": 0, "stations": 0, "qsos": 0}
        for raw in all_callsigns:
            try:
                qsos = self._import_one(
                    contest=contest,
                    raw_call=raw,
                    user_row=users_by_call.get(raw),
                    station_row=stations_by_call.get(raw),
                    log_rows=logs_by_call.get(raw, []),
                    append_p=append_p,
                    db_name=db_path.name,
                )
            except (ValueError, sqlite3.Error) as exc:
                self.stdout.write(self.style.WARNING(f"  ! {raw}: {exc}"))
                continue

            counts["participants"] += 1
            if raw in stations_by_call:
                counts["stations"] += 1
            counts["qsos"] += qsos
            self.stdout.write(f"  + {raw}: {qsos} QSOs")

        self.stdout.write(self.style.SUCCESS(
            f"Imported into NMD {year}: "
            f"{counts['participants']} participants, "
            f"{counts['stations']} station descriptions, "
            f"{counts['qsos']} QSOs."
        ))

    # --- legacy DB loaders -------------------------------------------------------------------

    @staticmethod
    def _load_users(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
        try:
            rows = conn.execute("SELECT * FROM user").fetchall()
        except sqlite3.Error:
            return {}
        return {normalize_callsign(r["callsign"]): r for r in rows if r["callsign"]}

    @staticmethod
    def _load_stations(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
        try:
            rows = conn.execute("SELECT * FROM station_description").fetchall()
        except sqlite3.Error:
            return {}
        return {normalize_callsign(r["callsign"]): r for r in rows if r["callsign"]}

    @staticmethod
    def _load_logs_grouped(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
        try:
            rows = conn.execute("SELECT * FROM log_entry ORDER BY callsign, utc, id").fetchall()
        except sqlite3.Error:
            return {}
        out: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            key = normalize_callsign(r["callsign"])
            if key:
                out.setdefault(key, []).append(r)
        return out

    # --- per-callsign import -----------------------------------------------------------------

    @transaction.atomic
    def _import_one(
        self, *, contest: Contest, raw_call: str,
        user_row: sqlite3.Row | None,
        station_row: sqlite3.Row | None,
        log_rows: list[sqlite3.Row],
        append_p: bool,
        db_name: str,
    ) -> int:
        normalized = normalize_callsign(raw_call)
        if not normalized:
            raise ValueError("empty callsign")

        username = login_username(normalized)
        if append_p and "/" not in normalized:
            participant_call = f"{normalized}/P"
        else:
            participant_call = normalized

        email = _first_nonempty(
            user_row["email"] if user_row else None,
            station_row["email"] if station_row else None,
            f"{username.lower()}@import.local",
        )
        first_name = _first_nonempty(
            station_row["opname"] if station_row else None,
            username,
        )

        user, _ = User.objects.get_or_create(
            username=username,
            defaults={"email": email, "first_name": first_name},
        )

        defaults = _participant_defaults_from_station(station_row)
        defaults.update({
            "callsign": participant_call,
            "first_name": first_name,
            "email": email,
            "operating_modes": _infer_modes(log_rows),
        })
        if station_row and bool(station_row["submitted"]):
            defaults["submitted_at"] = timezone.now()
        else:
            defaults["submitted_at"] = None

        participant, created = Participant.objects.get_or_create(
            contest=contest, user=user, defaults=defaults,
        )
        if not created:
            # Re-running the importer should refresh the row.
            for k, v in defaults.items():
                setattr(participant, k, v)
            participant.save()

        if station_row is not None:
            _upsert_station(participant, station_row)

        qso_rows = [
            {
                "utc": (r["utc"] or "").strip(),
                "remote_call": (r["remote_call"] or "").strip(),
                "rsts": (r["rsts"] or "").strip(),
                "txts": (r["txts"] or "").strip(),
                "rstr": (r["rstr"] or "").strip(),
                "txtr": (r["txtr"] or "").strip(),
            }
            for r in log_rows
        ]
        return replace_qsos_from_upload(
            participant=participant, rows=qso_rows, filename=db_name,
        )


# --- module helpers ---------------------------------------------------------------------------


def _first_nonempty(*values) -> str:
    for v in values:
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _infer_modes(log_rows: list[sqlite3.Row]) -> int:
    """Bitmask from RST lengths across the participant's QSOs.

    RST length 2 = SSB (bit 2), 3 = CW (bit 1). A station that logged
    only CW gets ``Mode.CW``, only SSB → ``Mode.SSB``, both → ``BOTH``.
    No logged QSOs → default ``BOTH`` (we don't know what they
    intended; let the operator narrow it later if they care).
    """
    if not log_rows:
        return Participant.Mode.BOTH
    has_cw = False
    has_ssb = False
    for r in log_rows:
        for rst in (r["rsts"], r["rstr"]):
            n = len((rst or "").strip())
            if n == 3:
                has_cw = True
            elif n == 2:
                has_ssb = True
    if has_cw and has_ssb:
        return Participant.Mode.BOTH
    if has_cw:
        return Participant.Mode.CW
    if has_ssb:
        return Participant.Mode.SSB
    return Participant.Mode.BOTH


def _participant_defaults_from_station(row: sqlite3.Row | None) -> dict:
    """Resolve the required-NOT-NULL columns on Participant from a
    legacy ``station_description`` row, falling back to stubs."""
    canton = _STUB_CANTON
    altitude = _STUB_ALTITUDE_M
    location_text = ""
    coord_e_raw, coord_n_raw = "", ""
    ch1903p_e = _STUB_CH1903P_E
    ch1903p_n = _STUB_CH1903P_N
    wgs84_lat = _STUB_WGS84_LAT
    wgs84_lon = _STUB_WGS84_LON
    coord_system_input = Participant.CoordSystem.CH1903PLUS

    if row is not None:
        if row["kanton"]:
            canton = (row["kanton"] or "").strip()[:2].upper() or _STUB_CANTON
        if row["qah"] is not None:
            try:
                altitude = max(0, int(row["qah"]))
            except (TypeError, ValueError):
                pass
        location_text = (row["ort"] or "").strip()
        kx, ky = (row["koord_x"] or "").strip(), (row["koord_y"] or "").strip()
        if kx and ky:
            coord_e_raw, coord_n_raw = kx, ky
            try:
                parsed = parse_coordinate_pair(kx, ky)
                ch1903p_e = parsed.ch1903p_e
                ch1903p_n = parsed.ch1903p_n
                wgs84_lat = parsed.wgs84_lat
                wgs84_lon = parsed.wgs84_lon
                coord_system_input = parsed.detected_system
            except CoordinateError:
                # Outside Switzerland or unparseable — keep raw input,
                # stub the canonical fields. The participant still
                # imports; ranking-page map just won't show them.
                pass

    return {
        "canton": canton,
        "altitude_m": altitude,
        "location_text": location_text,
        "coord_system_input": coord_system_input,
        "coord_input_e": coord_e_raw,
        "coord_input_n": coord_n_raw,
        "ch1903p_e": ch1903p_e,
        "ch1903p_n": ch1903p_n,
        "wgs84_lat": wgs84_lat,
        "wgs84_lon": wgs84_lon,
    }


def _upsert_station(participant: Participant, row: sqlite3.Row) -> None:
    """Write the legacy station fields onto the Participant directly
    (Participant + StationDescription were merged in migration 0007)
    and rebuild the 11 component slots."""
    participant.op_name = (row["opname"] or "").strip()
    participant.watt = (row["watt"] or "").strip()
    try:
        participant.total_weight_g = max(0, int(row["gesamtegewicht"] or 0))
    except (TypeError, ValueError):
        participant.total_weight_g = 0
    participant.save(update_fields=["op_name", "watt", "total_weight_g"])

    # Wipe and re-create components so re-running the importer doesn't
    # leave stale rows behind.
    StationComponent.objects.filter(participant=participant).delete()
    for i in range(1, COMPONENT_SLOTS + 1):
        bez = (row[f"sta{i:02d}bez"] or "").strip()
        try:
            gramm = max(0, int(row[f"sta{i:02d}gramm"] or 0))
        except (TypeError, ValueError):
            gramm = 0
        if not bez and gramm == 0:
            continue
        StationComponent.objects.create(
            participant=participant, idx=i, description=bez, weight_g=gramm,
        )

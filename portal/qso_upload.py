"""CSV/.nmd file upload parsing for the QSO log.

Matches the legacy nmdlogsubmission format:

- 6 columns, semicolon-separated: ``UTC;CALL;RSTS;TXTS;RSTR;TXTR``.
- Optional station-info comment lines starting with ``#;`` (parsed but not
  consumed here — M2.4 will handle the StationDescription side).
- Encodings: BOM-aware UTF-8, with Latin-1 / CP1252 fallback.
- Excel quirks tolerated: ``="0612"`` and ``=0612`` UTC quoting, leading-zero
  drop (``950`` → ``0950``), embedded tabs.
- Tabs are replaced with spaces before line splitting (the legacy parser
  did this — some loggers emit tab-prefixed fields).

The CSV wire format is fixed (see CLAUDE.md). Do NOT add new columns.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class StationInfo:
    """Raw station-info key/value pairs from ``#;FIELD=;value`` comment lines.

    M2.3 doesn't consume these — the upload only replaces QSOs. M2.4 will use
    the same parser to populate StationDescription / StationComponent.
    """
    fields: dict[str, str] = field(default_factory=dict)

    def update(self, name: str, value: str) -> None:
        self.fields[name.strip().upper()] = value.strip()


@dataclass
class ParsedUpload:
    qsos: list[dict[str, str]]
    station_info: StationInfo
    skipped_lines: list[str] = field(default_factory=list)


class UploadParseError(Exception):
    """Surfaceable parsing error (encoding etc.)."""


_ENCODINGS = ("utf-8-sig", "utf-8", "latin1", "cp1252")


def _decode(blob: bytes) -> str:
    last_err: UnicodeDecodeError | None = None
    for enc in _ENCODINGS:
        try:
            return blob.decode(enc)
        except UnicodeDecodeError as exc:
            last_err = exc
    raise UploadParseError(
        "Could not decode file. Use UTF-8, Latin-1, or Windows-1252 encoding."
    ) from last_err


def _clean_utc(raw: str) -> str:
    """Strip Excel formula prefixes and pad to HHMM."""
    s = (raw or "").strip()
    if s.startswith('="') and s.endswith('"'):
        s = s[2:-1]
    elif s.startswith("="):
        s = s[1:]
    s = s.strip('"').strip()
    if len(s) == 3 and s.isdigit():
        s = "0" + s
    return s


def _split_station_info(line: str) -> tuple[str, str] | None:
    """``#;ORT=;Pilatus`` → (``ORT``, ``Pilatus``). Returns None if malformed."""
    body = line[2:] if line.startswith("#;") else line
    body = body.replace("\t", " ")
    if "=;" not in body:
        return None
    name, _, value = body.partition("=;")
    return name.strip(), value.strip()


_QSO_FIELDS = ("UTC", "CALL", "RSTS", "TXTS", "RSTR", "TXTR")


def parse_upload(blob: bytes) -> ParsedUpload:
    """Decode and parse a .nmd / .csv upload into normalized QSO dicts."""
    content = _decode(blob).replace("\t", " ")
    station = StationInfo()
    qsos: list[dict[str, str]] = []
    skipped: list[str] = []
    qso_lines: list[str] = []

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#;"):
            kv = _split_station_info(stripped)
            if kv is not None:
                station.update(*kv)
            continue
        if stripped.startswith("#"):
            continue  # plain comment
        qso_lines.append(line)

    if not qso_lines:
        return ParsedUpload(qsos=qsos, station_info=station, skipped_lines=skipped)

    reader = csv.DictReader(
        io.StringIO("\n".join(qso_lines)),
        delimiter=";",
        fieldnames=list(_QSO_FIELDS),
    )
    for row_no, row in enumerate(reader, start=1):
        if all(not (v or "").strip() for v in row.values()):
            continue
        utc = _clean_utc(row.get("UTC") or "")
        qsos.append({
            "utc": utc,
            "remote_call": (row.get("CALL") or "").strip().strip('"'),
            "rsts": (row.get("RSTS") or "").strip().strip('"'),
            "txts": (row.get("TXTS") or "").strip().strip('"'),
            "rstr": (row.get("RSTR") or "").strip().strip('"'),
            "txtr": (row.get("TXTR") or "").strip().strip('"'),
        })

    return ParsedUpload(qsos=qsos, station_info=station, skipped_lines=skipped)


def iter_qsos(blob: bytes) -> Iterable[dict[str, str]]:
    return parse_upload(blob).qsos

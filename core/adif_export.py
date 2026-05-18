"""Export a participant's submitted QSO log as ADIF.

ADIF (Amateur Data Interchange Format) is the standard format ham
loggers use to exchange contact logs. The file is plain UTF-8 text
with self-describing tags::

    <CALL:6>HB9TVK <QSO_DATE:8>20250720 <TIME_ON:4>0700 ... <EOR>

Per :ref:`scope decisions`, only standard ADIF fields are emitted —
no app-defined scoring annotations — so any logger can ingest the
file unmodified.

Cleaning policy matches the M3 scoring engine: rows without a
parseable ``utc_time`` or a recognised ``mode`` are silently skipped.
The user filed those as drafts; they were never scored, and exporting
them would just confuse the receiving logger.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .models import Participant, QsoEntry

ADIF_VERSION = "3.1.4"
PROGRAM_ID = "NMDSuite"
BAND = "80M"
CONTEST_ID = "USKA-NMD"


def build_participant_adif(participant: Participant) -> str:
    """Render ``participant``'s submitted log as ADIF text.

    Returns a complete file body (header + records) ready to be
    served as a download. Always ends with a trailing newline so
    operators concatenating multiple ADIFs don't get records
    glued together.
    """
    out: list[str] = [_header_block(participant)]
    qsos = (
        participant.qsos
        .filter(utc_time__isnull=False)
        .exclude(mode="")
        .order_by("utc_time", "id")
    )
    for qso in qsos:
        out.append(_qso_record(participant, qso))
    return "\n".join(out) + "\n"


def _header_block(participant: Participant) -> str:
    """Free-text comment + standard preamble tags + ``<EOH>``."""
    comment = (
        f"NMDSuite ADIF export — {participant.callsign} — "
        f"NMD {participant.contest.year}"
    )
    parts = [
        comment,
        _tag("ADIF_VER", ADIF_VERSION),
        _tag("PROGRAMID", PROGRAM_ID),
        _tag("CREATED_TIMESTAMP", datetime.now(timezone.utc).strftime("%Y%m%d %H%M%S")),
        "<EOH>",
    ]
    return "\n".join(parts)


def _qso_record(participant: Participant, qso: QsoEntry) -> str:
    """One ADIF QSO record, tags on a single line ending in ``<EOR>``."""
    when = qso.utc_time
    fields: list[tuple[str, str]] = [
        ("CALL", qso.remote_call),
        ("QSO_DATE", when.strftime("%Y%m%d")),
        ("TIME_ON", when.strftime("%H%M")),
        ("BAND", BAND),
        ("MODE", qso.mode),
        ("RST_SENT", qso.rsts),
        ("RST_RCVD", qso.rstr),
    ]
    if qso.txts:
        fields.append(("STX_STRING", qso.txts))
    if qso.txtr:
        fields.append(("SRX_STRING", qso.txtr))
    fields.extend([
        ("CONTEST_ID", CONTEST_ID),
        ("OPERATOR", participant.callsign),
        ("STATION_CALLSIGN", participant.callsign),
    ])
    return " ".join(_tag(name, value) for name, value in fields) + " <EOR>"


def _tag(name: str, value: str) -> str:
    """Format one ADIF tag. Length is the UTF-8 byte count of the
    value (not character count) — important for umlauts."""
    encoded = (value or "").encode("utf-8")
    return f"<{name}:{len(encoded)}>{value or ''}"

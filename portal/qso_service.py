"""Persistence layer for QSO log entries.

Permissive: anything the operator types is stored verbatim. ``utc_time`` and
``mode`` are filled only when the corresponding raw fields parse cleanly;
otherwise they stay null/blank and the per-field validity properties on
``QsoEntry`` flag them as invalid in the UI.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

from core.models import Contest, Participant, QsoEntry
from registration.callsigns import normalize_callsign

from .qso_validators import is_valid_rst, is_valid_utc, mode_from_rsts


def _utc_to_datetime(contest: Contest, hhmm: str) -> datetime:
    hour, minute = int(hhmm[:2]), int(hhmm[2:])
    return datetime.combine(contest.contest_date, time(hour, minute, 0, tzinfo=timezone.utc))


def _normalize_utc(raw: str) -> str:
    """Pad to HHMM if 3 digits supplied (legacy loggers strip the leading zero)."""
    raw = (raw or "").strip()
    return f"0{raw}" if len(raw) == 3 and raw.isdigit() else raw


def _apply(entry: QsoEntry, *, contest: Contest, data: dict[str, Any]) -> QsoEntry:
    utc_raw = _normalize_utc(data.get("utc", ""))
    entry.utc_raw = utc_raw
    entry.utc_time = _utc_to_datetime(contest, utc_raw) if is_valid_utc(utc_raw) else None

    rsts = (data.get("rsts") or "").strip()
    rstr = (data.get("rstr") or "").strip()
    entry.rsts = rsts
    entry.rstr = rstr
    entry.mode = mode_from_rsts(rsts) if is_valid_rst(rsts) else ""

    entry.remote_call = normalize_callsign(data.get("remote_call", ""))
    entry.txts = (data.get("txts") or "").strip()
    entry.txtr = (data.get("txtr") or "").strip()
    return entry


def is_all_empty(data: dict[str, Any]) -> bool:
    return not any((data.get(k) or "").strip() for k in ("utc", "remote_call", "rsts", "txts", "rstr", "txtr"))


def create_qso(*, participant: Participant, data: dict[str, Any]) -> QsoEntry:
    entry = QsoEntry(participant=participant)
    _apply(entry, contest=participant.contest, data=data)
    entry.save()
    return entry


def update_qso(*, qso: QsoEntry, data: dict[str, Any]) -> QsoEntry:
    _apply(qso, contest=qso.participant.contest, data=data)
    qso.save()
    return qso


def list_qsos(participant: Participant):
    return QsoEntry.objects.filter(participant=participant).order_by("utc_raw", "id")


def initial_from_qso(qso: QsoEntry) -> dict[str, str]:
    return {
        "utc": qso.utc_raw,
        "remote_call": qso.remote_call,
        "rsts": qso.rsts,
        "txts": qso.txts,
        "rstr": qso.rstr,
        "txtr": qso.txtr,
    }

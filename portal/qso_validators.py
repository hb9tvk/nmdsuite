"""Validation helpers for log entries.

Mirrors the rules from the contest reglement (§7) and the legacy submission
app's `validate_*` functions, with charset tightened to the official rule:
``[a-z 0-9 . - / ?]`` only, and minimum text length of 15 characters
(spaces don't count).
"""
from __future__ import annotations

import re

# Allowed characters in the exchanged text per rule §7.
_TEXT_CHARSET = re.compile(r"^[a-z0-9 .\-/?]*$", flags=re.IGNORECASE)
_MIN_TEXT_CHARS = 15

_UTC_RE = re.compile(r"^\d{4}$")
_RST_RE = re.compile(r"^\d{2,3}$")


def is_valid_utc(value: str) -> bool:
    """``HHMM`` between 0600 and 0959 inclusive (the contest window)."""
    if not value or not _UTC_RE.match(value):
        return False
    hour, minute = int(value[:2]), int(value[2:])
    if not (6 <= hour <= 9):
        return False
    return 0 <= minute <= 59


def is_valid_rst(value: str) -> bool:
    """RST report: 2 digits (SSB) or 3 digits (CW)."""
    return bool(value) and bool(_RST_RE.match(value))


def is_text_payload_valid(text: str) -> bool:
    """An empty text is fine (non-NMD QSOs only exchange RST). If present,
    enforce the rule §7 charset and ``>= 15`` non-space characters."""
    if not text:
        return True
    if not _TEXT_CHARSET.match(text):
        return False
    return len(text.replace(" ", "")) >= _MIN_TEXT_CHARS


def mode_from_rsts(rsts: str) -> str:
    """Derive the QSO mode from the RST-sent length.

    The .nmd CSV format never carries an explicit mode column, so we use the
    same rule the legacy TCL scoring app uses: 3 digits → CW, 2 digits → SSB.
    """
    return "CW" if len(rsts) == 3 else "SSB"

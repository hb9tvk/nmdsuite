"""Callsign helpers.

The same callsign can show up in registration as ``HB9TVK``, ``HB9TVK/P`` or
even ``HB9TVK/QRP``. For login (``User.username``) we always strip the
trailing single-letter postfix so that the operator can sign in with their
"normal" callsign. The ``Participant.callsign`` field, in contrast, keeps
whatever the operator typed — that's what they intend to use on air.
"""
from __future__ import annotations

import re

# Permissive validator inherited from the legacy Flask app, lightly tightened:
# - One root callsign of letters/digits.
# - Optional country prefix segment (e.g. "OE/HB9TVK") OR portable postfix.
# - Optional final single-letter postfix (/P, /M, /MM, ...).
# Not strict ITU validation; that would reject some real-world edge cases.
_CALLSIGN_RE = re.compile(r"^[A-Z0-9]+(/[A-Z0-9]+)?(/[A-Z]{1,2})?$")


def normalize_callsign(raw: str) -> str:
    """Trim and uppercase. No structural changes."""
    return (raw or "").strip().upper()


def is_valid_callsign(raw: str) -> bool:
    return bool(_CALLSIGN_RE.match(normalize_callsign(raw)))


def login_username(raw: str) -> str:
    """Drop the trailing /-suffix so login uses the bare callsign.

    ``HB9TVK/P``  → ``HB9TVK``
    ``HB9TVK``    → ``HB9TVK``
    ``OE/HB9TVK/P`` → ``OE/HB9TVK``
    """
    call = normalize_callsign(raw)
    parts = call.split("/")
    # Drop the *final* segment only if it's a short letter-only suffix (P, M, MM…).
    if len(parts) >= 2 and parts[-1].isalpha() and len(parts[-1]) <= 2:
        return "/".join(parts[:-1])
    return call

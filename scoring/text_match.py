"""Text-comparison helpers for the scoring engine (M3.1).

When two participants log the same NMD QSO, the text *sent* by one operator
should equal the text *received* by the other. In practice it doesn't always:
operators mis-hear a character, transpose two characters, or drop one.

The legacy TCL implementation accepted only strict equality after stripping
all whitespace and uppercasing (see ``reference/scoring_tcl/cgi-bin/nmdaw.wsh``
``proc checkTxt``). That was too rigid — established practice (and the
referee's manual checks) tolerated up to 2 character errors. M3.1 makes that
tolerance explicit so the pairing engine in M3.2 can use it consistently.

The functions here only compute distances; they do **not** decide what to do
with the result. That decision (``full_match`` vs ``text_mismatch`` vs
``unmatched``) belongs to the pairing engine in M3.2.
"""
from __future__ import annotations

# Established practice from prior contests: up to 2 character errors (per
# direction) is treated as a clean NMD match. Anything more counts as a
# text-mismatch — still a valid QSO, but flagged.
DEFAULT_MAX_ERRORS = 2


def normalize_exchange(text: str) -> str:
    """Canonicalise an exchange-text payload for comparison.

    Operators are inconsistent with spacing and case. The legacy TCL scorer
    stripped *all* whitespace (not just leading/trailing) and uppercased
    before comparing; we keep that behaviour so historic logs score the same.
    """
    if not text:
        return ""
    # str.split() with no args splits on runs of whitespace incl. tabs / NBSP,
    # which is exactly what we want — then rejoin with no separator.
    return "".join(text.split()).upper()


def text_distance(a: str, b: str) -> int:
    """Levenshtein edit distance between two exchange texts.

    Both inputs are run through :func:`normalize_exchange` first, so callers
    don't have to. Each substitution / insertion / deletion costs 1 — matching
    the "wrong or missing character" definition used by referees.
    """
    s1 = normalize_exchange(a)
    s2 = normalize_exchange(b)
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    # Iterative two-row DP. O(len(s1) * len(s2)) time, O(min(len)) space.
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    previous = list(range(len(s2) + 1))
    for i, ch1 in enumerate(s1, start=1):
        current = [i] + [0] * len(s2)
        for j, ch2 in enumerate(s2, start=1):
            cost = 0 if ch1 == ch2 else 1
            current[j] = min(
                current[j - 1] + 1,          # insertion
                previous[j] + 1,             # deletion
                previous[j - 1] + cost,      # substitution / match
            )
        previous = current
    return previous[-1]


def texts_match(a: str, b: str, *, max_errors: int = DEFAULT_MAX_ERRORS) -> bool:
    """``True`` iff ``a`` and ``b`` are within ``max_errors`` characters."""
    return text_distance(a, b) <= max_errors

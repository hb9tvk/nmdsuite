"""Generate the post-contest ranking map as a PDF.

The published counterpart to :mod:`core.participant_map_pdf`: the same
relief map and the same circles, but drawn once the results are public
and carrying each station's placing in its label. Sits beside
:mod:`public.ranking_pdf` — same data, drawn instead of tabulated — and
is produced at publish time.

The legacy version ("Karte_RangL") was drawn by hand from the finished
ranking. It squeezed both placings into the label rather than printing a
separate table, which is what keeps the sheet readable:

    <rank CW>. <callsign> <rank SSB>.

Either side is dropped when the station does not appear in that mode's
ranking, so a CW-only station reads ``3. HB9XYZ`` and an SSB-only one
``HB9XYZ 7.``. The heading spells the convention out for the reader.

Decisions encoded here:

- **Only ranked stations are drawn.** A station with no placing in either
  mode carries no result, and a bare callsign on a sheet titled
  "Rangliste" reads as a rank the reader simply cannot find. Registered
  stations that did not score are on the participant map instead. Note
  that :func:`public.ranking_service.build_ranking_page` already limits
  itself to participants who submitted a log and were not cancelled, and
  ranks by score alone.
- **Ranks come from the ranking page**, not recomputed here, so the map
  can never disagree with the published tables or the ranking PDF.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.map_render import MapStation, render_station_map
from core.models import Contest, Participant

from .ranking_service import build_ranking_page

# One artefact goes to every participant regardless of locale — see the
# note on hardcoded trilingual text in core.map_render.
SUBTITLES = (
    "Rangliste und Standorte",
    "Classement et QTH",
    "Classifica e QTH",
)

# How to read a label — one line per language, like every other block on
# the sheet. The legacy map wrote the middle term as the Q-code "QRA",
# which would make the German and French lines identical; spelling out
# each language's own word for "callsign" keeps all three visible, so a
# French reader isn't left wondering whether their line was forgotten.
LEGEND = (
    "<Rang CW> Rufzeichen <Rang SSB>",
    "<Rang CW> Indicatif <Rang SSB>",
    "<Rango CW> Nominativo <Rango SSB>",
)


@dataclass(frozen=True)
class StationRanks:
    """A station's placing in each mode. ``None`` means "not ranked here"."""

    callsign: str
    cw: int | None
    ssb: int | None


def rank_label(ranks: StationRanks) -> str:
    """The label text: ``"4. HB9BXE 12."``, either rank omitted if absent.

    Both ranks carry the trailing period that marks them as ordinals in
    all three languages, which is also what tells the reader that the
    number after the callsign is a rank and not a score.
    """
    parts: list[str] = []
    if ranks.cw is not None:
        parts.append(f"{ranks.cw}.")
    parts.append(ranks.callsign)
    if ranks.ssb is not None:
        parts.append(f"{ranks.ssb}.")
    return " ".join(parts)


def station_ranks(contest: Contest) -> list[StationRanks]:
    """Every station holding at least one placing, ordered by callsign.

    Callsign order is arbitrary as far as the reader is concerned — it
    only fixes the label placer's tie-breaking, so the same results always
    produce the same sheet.
    """
    page = build_ranking_page(contest)
    cw_by_call = {row.callsign: row.rank for row in page.cw}
    ssb_by_call = {row.callsign: row.rank for row in page.ssb}

    return [
        StationRanks(
            callsign=callsign,
            cw=cw_by_call.get(callsign),
            ssb=ssb_by_call.get(callsign),
        )
        for callsign in sorted(cw_by_call.keys() | ssb_by_call.keys())
    ]


def _stations(contest: Contest) -> list[MapStation]:
    """Ranked stations resolved to labels and positions.

    A ranked station whose coordinates are missing is skipped, matching
    the participant map — better an absent circle than one in the sea.
    """
    positions = {
        p.callsign: p
        for p in Participant.objects.filter(
            contest=contest, cancelled_at__isnull=True,
        )
    }
    out: list[MapStation] = []
    for ranks in station_ranks(contest):
        p = positions.get(ranks.callsign)
        if p is None or p.ch1903_e is None or p.ch1903_n is None:
            continue
        out.append(MapStation(
            label=rank_label(ranks), east=p.ch1903_e, north=p.ch1903_n,
        ))
    return out


def build_ranking_map_pdf(contest: Contest) -> bytes:
    """Render the ranking map for ``contest`` as a PDF."""
    return render_station_map(
        year=contest.year,
        stations=_stations(contest),
        subtitles=SUBTITLES,
        legend=LEGEND,
        doc_title=f"NMD {contest.year} — ranking map",
    )

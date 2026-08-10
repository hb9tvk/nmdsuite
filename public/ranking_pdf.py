"""Generate the public ranking as a printable PDF (F4).

For publishing the contest results in the USKA/HTC club magazine, the
admin UI exposes a downloadable version of the same data the public
ranking page shows — minus the interactive map — laid out for paper.

Layout: A4, three tables, in two orientations.

    CW ranking       ┐ portrait
    SSB ranking      ┘
    Station data     — landscape, from a fresh page, over as many
                       pages as it needs

The station table carries the operator's whole kit list, which does not
fit across a portrait sheet, so it turns the paper rather than shrinking
the type.

Uses the same :class:`~public.ranking_service.RankingPage` payload the
ranking template renders, so the PDF and the live page can never
drift on what counts as which category.
"""
from __future__ import annotations

from io import BytesIO
from typing import NamedTuple
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from core.models import Contest

from .ranking_service import (
    ANTENNA_LABEL,
    FEEDLINE_LABEL,
    GUYING_LABEL,
    MASTS_LABEL,
    PSU_LABEL,
    TRX_LABEL,
    RankingPage,
    build_ranking_page,
)

MARGIN = 12 * mm


class _CellStyles(NamedTuple):
    """The paragraph styles the table builders draw cells with."""

    body: ParagraphStyle
    header: ParagraphStyle
    header_right: ParagraphStyle


def build_ranking_pdf(contest: Contest, *, page: RankingPage | None = None) -> bytes:
    """Render the ranking + station data for ``contest`` as a PDF blob.

    ``page`` is accepted for callers that already built the payload
    (e.g. the admin preview). If omitted we build it here so the
    function is usable standalone in management commands or tests.
    """
    if page is None:
        page = build_ranking_page(contest)

    buffer = BytesIO()
    doc = _document(buffer, title=f"NMD {contest.year} — Ranking")

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle(
        "NMDTitle", parent=styles["Title"], fontSize=14, leading=18, alignment=1,
    )
    sub = ParagraphStyle(
        "NMDSub", parent=styles["Normal"], fontSize=10, leading=13, alignment=1,
    )
    h2 = ParagraphStyle(
        "NMDH2", parent=styles["Heading2"], fontSize=11, leading=14, spaceBefore=4 * mm,
    )
    foot = ParagraphStyle(
        "NMDFoot", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.grey,
    )
    cell_wrap = ParagraphStyle(
        "NMDCellWrap", parent=styles["Normal"], fontSize=8, leading=9,
    )
    # Header cells are paragraphs, not plain strings: the component labels
    # come from the operator's own edit form and run long once translated,
    # so they have to wrap inside their column instead of over it.
    header_wrap = ParagraphStyle(
        "NMDHeaderWrap", parent=cell_wrap, fontName="Helvetica-Bold",
    )
    cells = _CellStyles(
        body=cell_wrap,
        header=header_wrap,
        header_right=ParagraphStyle(
            "NMDHeaderWrapRight", parent=header_wrap, alignment=TA_RIGHT,
        ),
    )

    story: list = []
    story.append(Paragraph(
        f"USKA / HTC — National Mountain Day {contest.year}", h1,
    ))
    story.append(Paragraph(
        f"Contest am {contest.contest_date} — "
        f"Stand {timezone.now().strftime('%Y-%m-%d %H:%M UTC')}",
        sub,
    ))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("CW", h2))
    story.append(_ranking_table(page.cw, cells))

    story.append(Paragraph("SSB", h2))
    story.append(_ranking_table(page.ssb, cells))

    # The station table turns the sheet, so it always starts a page of its
    # own; the template switch only takes effect at the next page break.
    story.append(NextPageTemplate("landscape"))
    story.append(PageBreak())
    story.append(Paragraph("Station data", h2))
    story.append(_station_data_table(page.stations, cells))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "nmd@uska.ch — NMD Kommission USKA/HTC",
        foot,
    ))

    doc.build(story)
    return buffer.getvalue()


# --- page setup ------------------------------------------------------------------------------


def _document(buffer: BytesIO, *, title: str) -> BaseDocTemplate:
    """A4 with two page templates the story switches between.

    ``SimpleDocTemplate`` would fix one orientation for the whole
    document; reportlab takes the page size from whichever
    :class:`PageTemplate` a page begins under, so the rankings can print
    portrait and the station data landscape in the same file.
    """
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title=title,
    )
    doc.addPageTemplates([
        _page_template("portrait", A4),
        _page_template("landscape", landscape(A4)),
    ])
    return doc


def _page_template(name: str, pagesize: tuple[float, float]) -> PageTemplate:
    width, height = pagesize
    frame = Frame(
        MARGIN, MARGIN,
        width - 2 * MARGIN, height - 2 * MARGIN,
        id=name,
    )
    return PageTemplate(id=name, frames=[frame], pagesize=pagesize)


# --- table builders --------------------------------------------------------------------------


def _ranking_table(rows, cells: _CellStyles) -> Table:
    labels = [
        "Rang", "Rufzeichen", "Standort", "QAH (m)",
        "NMD", "HB", "EU", "QSO", "Punkte",
    ]
    num_cols = {0, 3, 4, 5, 6, 7, 8}
    data: list[list] = [_header_row(labels, cells, num_cols)]
    for r in rows:
        data.append([
            str(r.rank),
            r.callsign,
            _cell(r.location_text, cells),
            str(r.altitude_m),
            str(r.nmd_qsos),
            str(r.hb_qsos),
            str(r.eu_qsos),
            str(r.total_qsos),
            str(r.points),
        ])
    if len(data) == 1:
        data.append(["—"] * len(labels))

    table = Table(
        data,
        colWidths=[
            10 * mm, 26 * mm, 56 * mm, 14 * mm,
            12 * mm, 12 * mm, 12 * mm, 14 * mm, 18 * mm,
        ],
        repeatRows=1,
    )
    table.setStyle(_table_style(num_cols=num_cols))
    return table


def _station_data_table(rows, cells: _CellStyles) -> Table:
    labels = [
        "Rufzeichen", "Punkte",
        str(TRX_LABEL), "Watt", str(PSU_LABEL), str(ANTENNA_LABEL),
        str(FEEDLINE_LABEL), str(MASTS_LABEL), str(GUYING_LABEL),
        "Gewicht (g)",
    ]
    num_cols = {1, 9}
    data: list[list] = [_header_row(labels, cells, num_cols)]
    for s in rows:
        data.append([
            s.callsign,
            str(s.points_total),
            _cell(s.trx, cells),
            s.watt or "",
            _cell(s.psu, cells),
            _cell(s.antenna, cells),
            _cell(s.feedline, cells),
            _cell(s.masts, cells),
            _cell(s.guying, cells),
            str(s.total_weight_g),
        ])
    if len(data) == 1:
        data.append(["—"] * len(labels))

    # Sums to the landscape frame's usable width (its own 6pt padding on
    # each side included), so the widest columns go to the free-text kit
    # descriptions.
    table = Table(
        data,
        colWidths=[
            23 * mm, 13 * mm, 34 * mm, 13 * mm, 30 * mm, 34 * mm,
            32 * mm, 32 * mm, 32 * mm, 16 * mm,
        ],
        repeatRows=1,
    )
    table.setStyle(_table_style(num_cols=num_cols))
    return table


def _header_row(labels, cells: _CellStyles, num_cols: set[int]) -> list:
    return [
        Paragraph(label, cells.header_right if i in num_cols else cells.header)
        for i, label in enumerate(labels)
    ]


def _cell(text: str, cells: _CellStyles):
    """A wrapping free-text cell.

    Empty string when there's nothing to say, so a blank slot costs no row
    height. Operator-typed kit descriptions are escaped: a paragraph reads
    its text as mini-HTML, and an ``&`` in "Dipol & Balun" would otherwise
    abort the whole render.
    """
    return Paragraph(escape(text), cells.body) if text else ""


def _table_style(*, num_cols: set[int]) -> TableStyle:
    """Shared table styling. ``num_cols`` is the set of column indexes that
    should be right-aligned (numeric)."""
    style = TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ])
    for col in sorted(num_cols):
        style.add("ALIGN", (col, 0), (col, -1), "RIGHT")
    return style

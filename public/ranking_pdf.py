"""Generate the public ranking as a printable PDF (F4).

For publishing the contest results in the USKA/HTC club magazine, the
admin UI exposes a downloadable version of the same data the public
ranking page shows — minus the interactive map — laid out for paper.

Layout: A4 portrait, three tables stacked vertically.

    CW ranking
    SSB ranking
    Station data

Uses the same :class:`~public.ranking_service.RankingPage` payload the
ranking template renders, so the PDF and the live page can never
drift on what counts as which category.
"""
from __future__ import annotations

from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.models import Contest

from .ranking_service import (
    ANTENNA_LABEL,
    PSU_LABEL,
    TRX_LABEL,
    RankingPage,
    build_ranking_page,
)


def build_ranking_pdf(contest: Contest, *, page: RankingPage | None = None) -> bytes:
    """Render the ranking + station data for ``contest`` as a PDF blob.

    ``page`` is accepted for callers that already built the payload
    (e.g. the admin preview). If omitted we build it here so the
    function is usable standalone in management commands or tests.
    """
    if page is None:
        page = build_ranking_page(contest)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=f"NMD {contest.year} — Ranking",
    )

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
    story.append(_ranking_table(page.cw, cell_wrap))

    story.append(Paragraph("SSB", h2))
    story.append(_ranking_table(page.ssb, cell_wrap))

    story.append(PageBreak())
    story.append(Paragraph("Station data", h2))
    story.append(_station_data_table(page.stations, cell_wrap))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "nmd@uska.ch — NMD Kommission USKA/HTC",
        foot,
    ))

    doc.build(story)
    return buffer.getvalue()


# --- table builders --------------------------------------------------------------------------


def _ranking_table(rows, cell_wrap) -> Table:
    header = [
        "Rang", "Rufzeichen", "Standort", "QAH (m)",
        "NMD", "HB", "EU", "QSO", "Punkte",
    ]
    data: list[list] = [header]
    for r in rows:
        data.append([
            str(r.rank),
            r.callsign,
            Paragraph(r.location_text or "", cell_wrap) if r.location_text else "",
            str(r.altitude_m),
            str(r.nmd_qsos),
            str(r.hb_qsos),
            str(r.eu_qsos),
            str(r.total_qsos),
            str(r.points),
        ])
    if len(data) == 1:
        data.append(["—"] * len(header))

    table = Table(
        data,
        colWidths=[
            10 * mm, 26 * mm, 56 * mm, 14 * mm,
            12 * mm, 12 * mm, 12 * mm, 14 * mm, 18 * mm,
        ],
        repeatRows=1,
    )
    table.setStyle(_table_style(num_cols={0, 3, 4, 5, 6, 7, 8}))
    return table


def _station_data_table(rows, cell_wrap) -> Table:
    header = [
        "Rufzeichen", "Punkte",
        str(TRX_LABEL), "Watt", str(PSU_LABEL), str(ANTENNA_LABEL),
        "Gewicht (g)",
    ]
    data: list[list] = [header]
    for s in rows:
        data.append([
            s.callsign,
            str(s.points_total),
            Paragraph(s.trx or "", cell_wrap) if s.trx else "",
            s.watt or "",
            Paragraph(s.psu or "", cell_wrap) if s.psu else "",
            Paragraph(s.antenna or "", cell_wrap) if s.antenna else "",
            str(s.total_weight_g),
        ])
    if len(data) == 1:
        data.append(["—"] * len(header))

    table = Table(
        data,
        colWidths=[
            24 * mm, 14 * mm, 38 * mm, 16 * mm, 32 * mm, 38 * mm, 18 * mm,
        ],
        repeatRows=1,
    )
    table.setStyle(_table_style(num_cols={1, 6}))
    return table


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

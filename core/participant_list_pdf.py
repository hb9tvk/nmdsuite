"""Generate the pre-contest participant list as a PDF (M4A.1).

The legacy version was a hand-edited PDF the NMD commission published
once registration closed; participants printed it and used it during
the contest to keep track of which stations they had worked. The
columns mirror the legacy layout:

    CW | SSB | QRA | QSO 1 | QSO 2 | Op | Coordinates | Site | Canton | QAH

CW / SSB are tick marks from ``Participant.operating_modes``. QSO 1/2
are empty boxes for the operator to fill in by hand. ``Op`` is the
participant's first name. ``Site`` is the station description's
``location_text`` if the operator has filled one in by the time we
generate; empty otherwise (the PDF is regenerated on each download).
"""
from __future__ import annotations

from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .models import Contest, Participant


def _coord_text(p: Participant) -> str:
    if p.ch1903_e is None or p.ch1903_n is None:
        return ""
    return f"{p.ch1903_e}/{p.ch1903_n}"


def _site_text(p: Participant) -> str:
    """Friendly location label, always present from registration."""
    return p.location_text or ""


def build_participant_list_pdf(contest: Contest) -> bytes:
    """Render the active-participants list for ``contest`` as a PDF."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"NMD {contest.year} — participant list",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle(
        "NMDTitle", parent=styles["Title"], fontSize=14, leading=18, alignment=1,
    )
    sub = ParagraphStyle(
        "NMDSub", parent=styles["Normal"], fontSize=10, leading=13, alignment=1,
    )
    foot = ParagraphStyle(
        "NMDFoot", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.grey,
    )
    site_style = ParagraphStyle(
        "NMDSite", parent=styles["Normal"], fontSize=9, leading=10,
    )

    story = []
    story.append(Paragraph("USKA / HTC — National Mountain Day", h1))
    story.append(Paragraph(
        "Angemeldete NMD-Stationen / "
        "Stations NMD inscrites / "
        f"Stazioni NMD iscritte — NMD {contest.year}",
        sub,
    ))
    story.append(Paragraph(
        "Stand / mise à jour / aggiornamento: "
        + timezone.now().strftime("%Y-%m-%d %H:%M UTC"),
        sub,
    ))
    story.append(Spacer(1, 6 * mm))

    header = [
        "CW", "SSB", "QRA", "QSO1", "QSO2", "Op", "Coordinates", "Site", "Kt.", "QAH",
    ]
    rows: list[list[str]] = [header]

    qs = (
        Participant.objects
        .filter(contest=contest, cancelled_at__isnull=True)
        .select_related("station")
        .order_by("callsign")
    )
    for p in qs:
        site = _site_text(p)
        rows.append([
            "✓" if p.operating_modes & 1 else "",
            "✓" if p.operating_modes & 2 else "",
            p.callsign,
            "",  # operator-fillable
            "",
            p.first_name,
            _coord_text(p),
            # Wrapped via Paragraph so long SOTA refs/location names break onto
            # a second line within the cell instead of bleeding into Kt.
            Paragraph(site, site_style) if site else "",
            p.canton,
            str(p.altitude_m),
        ])

    table = Table(
        rows,
        colWidths=[
            8 * mm, 9 * mm, 22 * mm,        # CW SSB QRA
            10 * mm, 10 * mm,                # QSO1 QSO2
            24 * mm,                          # Op
            27 * mm,                          # Coordinates
            50 * mm,                          # Site
            8 * mm, 10 * mm,                  # Kt. QAH
        ],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ("ALIGN", (0, 0), (4, -1), "CENTER"),      # CW, SSB, QRA, QSO1, QSO2 columns
        ("ALIGN", (8, 0), (9, -1), "RIGHT"),       # canton, altitude
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
        # Tight padding so the typical ~50-participant list fits on one A4 page.
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(table)

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "nmd@uska.ch — Viel Glück / bonne chance / in bocca al lupo!",
        foot,
    ))

    doc.build(story)
    return buffer.getvalue()

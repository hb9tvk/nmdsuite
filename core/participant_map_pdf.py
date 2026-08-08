"""Generate the pre-contest participant map as a PDF.

Sister to :mod:`core.participant_list_pdf` — same data, drawn instead of
tabulated. One circle per registered station at its own position on a
relief map of Switzerland, callsign printed beside it.

The legacy version was drawn by hand by a commission member after
registration closed. This one is regenerated on every download, so it is
always current.

Decisions encoded here:

- **Circle diameter is the QRB threshold, not a fixed number.** Circles
  are drawn at :data:`registration.forms.QRB_THRESHOLD_M` across, so two
  circles overlap on the map exactly when the registration form warned
  the operator about a nearby station. The legacy map used a 3.6 km
  convention that meant nothing in particular; tying the diameter to the
  constant keeps map and rule from ever drifting apart.
- **Trilingual, hardcoded.** Like the registration confirmation email,
  one artefact goes to every participant regardless of locale, so the
  title and legend carry DE/FR/IT rather than going through gettext.
- **Georeference comes from two surveyed points**, not from an assumed
  extent — see :class:`GeoReference`.
- **Callsigns are sized off the circles**, not set in fixed points, so
  circle and text stay in proportion however the sheet is scaled — see
  :func:`_label_metrics`.
- **Label placement is delegated** to :mod:`core.label_placement`, which
  knows nothing about maps. Everything geographic is resolved to canvas
  units first.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas as pdfcanvas

from registration.forms import QRB_THRESHOLD_M

from .label_placement import Rect, Station, place_labels
from .models import Contest, Participant

# --- the background image and its georeference ------------------------------------------------

IMAGE_PATH: Path = Path(settings.BASE_DIR) / "static" / "img" / "swissmap.png"
IMAGE_WIDTH_PX = 1123
IMAGE_HEIGHT_PX = 794

# Two surveyed pixel/coordinate pairs. Pixel origin is the image's top-left
# corner with y growing downward; coordinates are Swiss CH1903 (LV03), the
# 6-digit grid, which is what ``Participant.ch1903_e/n`` expose.
#
# The x and y scales these imply differ by 0.29%. A map projection is
# isotropic, so that is measurement noise — it is within +/-1 pixel of
# reading accuracy on both baselines. We keep the exact transform the two
# points define rather than silently averaging it away, and use the mean
# scale only where anisotropy would be visible (see ``metres_to_canvas``).
_CAL_A_PX = (263.0, 660.0)
_CAL_A_LV03 = (570082.0, 109691.0)
_CAL_B_PX = (755.0, 306.0)
_CAL_B_LV03 = (725955.0, 221524.0)

_M_PER_PX_X = (_CAL_B_LV03[0] - _CAL_A_LV03[0]) / (_CAL_B_PX[0] - _CAL_A_PX[0])
# Northing grows as pixel y shrinks, hence the reversed subtraction.
_M_PER_PX_Y = (_CAL_B_LV03[1] - _CAL_A_LV03[1]) / (_CAL_A_PX[1] - _CAL_B_PX[1])

# Coordinates of the image's top-left pixel.
_EAST_AT_PX0 = _CAL_A_LV03[0] - _CAL_A_PX[0] * _M_PER_PX_X
_NORTH_AT_PX0 = _CAL_A_LV03[1] + _CAL_A_PX[1] * _M_PER_PX_Y


@dataclass(frozen=True)
class GeoReference:
    """Maps Swiss LV03 coordinates onto a PDF canvas.

    Two transforms in one: LV03 to image pixels (from the surveyed points
    above), then image pixels to canvas points. The canvas is y-up with its
    origin bottom-left, the image is y-down from top-left, so the vertical
    axis flips on the way through.
    """

    canvas_width: float
    canvas_height: float

    @property
    def _scale_x(self) -> float:
        """Canvas points per image pixel, horizontally."""
        return self.canvas_width / IMAGE_WIDTH_PX

    @property
    def _scale_y(self) -> float:
        return self.canvas_height / IMAGE_HEIGHT_PX

    def to_canvas(self, east: float, north: float) -> tuple[float, float]:
        """LV03 easting/northing to canvas point coordinates."""
        px_x = (east - _EAST_AT_PX0) / _M_PER_PX_X
        px_y = (_NORTH_AT_PX0 - north) / _M_PER_PX_Y
        return px_x * self._scale_x, self.canvas_height - px_y * self._scale_y

    def metres_to_canvas(self, metres: float) -> float:
        """A ground distance as a canvas length.

        Uses the mean of the two axis scales: a circle drawn with a
        per-axis radius would come out as a (very slightly) squashed
        ellipse, which is worse than the 0.14% radius error this costs.
        """
        per_px = (_M_PER_PX_X + _M_PER_PX_Y) / 2.0
        mean_scale = (self._scale_x + self._scale_y) / 2.0
        return metres / per_px * mean_scale


# --- drawing constants ------------------------------------------------------------------------

LABEL_FONT = "Helvetica-Bold"
# Printed text height as a fraction of the circle diameter. The labels are
# sized against the circles rather than fixed in points, so the two keep
# their proportion whatever the page size or QRB_THRESHOLD_M do.
LABEL_HEIGHT_OF_DIAMETER = 0.80
# Cap height of LABEL_FONT as a fraction of the em, from its AFM metrics.
# Callsigns are uppercase letters and digits, so this — not the em that a
# PDF calls the "font size" — is the text height a reader actually sees.
# Update alongside LABEL_FONT.
LABEL_CAP_HEIGHT_RATIO = 0.718
# Row pitch as a multiple of the font size. Becomes the label rect's
# height, which is also the step label_placement stacks crowded labels by.
LABEL_LINE_SPACING = 1.17
TITLE_FONT = "Helvetica-Bold"
FOOT_FONT = "Helvetica"

# Keep labels off the very edge of the sheet.
PLACEMENT_INSET = 4.0

# Grid every 100 km, matching the legacy map's 500/600/700/800 rulers.
GRID_STEP_M = 100_000


def _label_metrics(geo: GeoReference) -> tuple[float, float]:
    """``(font_size, line_height)`` for the callsign labels, in points.

    Solves for the font size whose cap height is
    :data:`LABEL_HEIGHT_OF_DIAMETER` of a station circle. Everything the
    reader compares — circle, text — then scales together.
    """
    diameter = geo.metres_to_canvas(QRB_THRESHOLD_M)
    font_size = diameter * LABEL_HEIGHT_OF_DIAMETER / LABEL_CAP_HEIGHT_RATIO
    return font_size, font_size * LABEL_LINE_SPACING


def _grid_values(low: float, high: float) -> list[int]:
    """Every ``GRID_STEP_M`` multiple strictly inside ``low``..``high``."""
    first = int(low // GRID_STEP_M) + 1
    last = int(high // GRID_STEP_M)
    return [v * GRID_STEP_M for v in range(first, last + 1)]


def _draw_grid(c: pdfcanvas.Canvas, geo: GeoReference) -> None:
    """Thin coordinate rulers with kilometre labels at the sheet edges."""
    east_min = _EAST_AT_PX0
    east_max = _EAST_AT_PX0 + IMAGE_WIDTH_PX * _M_PER_PX_X
    north_max = _NORTH_AT_PX0
    north_min = _NORTH_AT_PX0 - IMAGE_HEIGHT_PX * _M_PER_PX_Y

    c.saveState()
    c.setStrokeColor(colors.Color(0, 0, 0, alpha=0.45))
    c.setLineWidth(0.4)
    c.setFont(FOOT_FONT, 7)
    c.setFillColor(colors.black)

    for east in _grid_values(east_min, east_max):
        x, _ = geo.to_canvas(east, north_min)
        c.line(x, 0, x, geo.canvas_height)
        c.drawString(x + 2, 6, str(east // 1000))

    for north in _grid_values(north_min, north_max):
        _, y = geo.to_canvas(east_min, north)
        c.line(0, y, geo.canvas_width, y)
        c.drawString(4, y + 3, str(north // 1000))

    c.restoreState()


def _draw_titles(c: pdfcanvas.Canvas, contest: Contest, geo: GeoReference) -> None:
    top = geo.canvas_height
    c.saveState()
    c.setFillColor(colors.black)
    c.setFont(TITLE_FONT, 15)
    c.drawString(28, top - 34, f"National Mountain Day {contest.year}")
    c.setFont(TITLE_FONT, 9)
    c.drawString(28, top - 50, "Angemeldete Stationen")
    c.drawString(28, top - 61, "Stations inscrites")
    c.drawString(28, top - 72, "Stazioni iscritte")
    c.setFont(FOOT_FONT, 7)
    c.drawString(
        28, top - 86,
        "Stand / mise à jour / aggiornamento: "
        + timezone.now().strftime("%Y-%m-%d %H:%M UTC"),
    )
    c.restoreState()


def _draw_footer(c: pdfcanvas.Canvas, geo: GeoReference) -> None:
    km = QRB_THRESHOLD_M / 1000
    diameter = f"{km:g} km"
    c.saveState()
    c.setFillColor(colors.black)
    c.setFont(FOOT_FONT, 7)
    c.drawString(28, 32, f"Stationskreise Ø {diameter}")
    c.drawString(28, 24, f"Cercles des stations Ø {diameter}")
    c.drawString(28, 16, f"Cerchi delle stazioni Ø {diameter}")
    c.setFillColor(colors.grey)
    c.drawRightString(geo.canvas_width - 12, 16, "© swisstopo")
    c.restoreState()


def _stations(
    contest: Contest,
    c: pdfcanvas.Canvas,
    geo: GeoReference,
    font_size: float,
    line_height: float,
):
    """Build the placement input from the contest's active registrations.

    Yields ``(participant, Station)``. Registrations without usable
    coordinates are skipped rather than drawn at the origin — a circle in
    the Mediterranean is worse than a missing one.
    """
    qs = (
        Participant.objects
        .filter(contest=contest, cancelled_at__isnull=True)
        .order_by("callsign")
    )
    radius = geo.metres_to_canvas(QRB_THRESHOLD_M / 2.0)
    index = 0
    for p in qs:
        east, north = p.ch1903_e, p.ch1903_n
        if east is None or north is None:
            continue
        x, y = geo.to_canvas(east, north)
        width = c.stringWidth(p.callsign, LABEL_FONT, font_size)
        yield p, Station(
            index=index,
            x=x,
            y=y,
            radius=radius,
            label_width=width,
            label_height=line_height,
        )
        index += 1


def build_participant_map_pdf(contest: Contest) -> bytes:
    """Render the active-participants map for ``contest`` as a PDF."""
    page_width, page_height = landscape(A4)
    buffer = BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=(page_width, page_height))
    c.setTitle(f"NMD {contest.year} — participant map")

    geo = GeoReference(canvas_width=page_width, canvas_height=page_height)

    if IMAGE_PATH.exists():
        c.drawImage(
            str(IMAGE_PATH), 0, 0,
            width=page_width, height=page_height,
            preserveAspectRatio=False, anchor="sw", mask="auto",
        )

    _draw_grid(c, geo)

    font_size, line_height = _label_metrics(geo)
    pairs = list(_stations(contest, c, geo, font_size, line_height))
    stations = [s for _p, s in pairs]

    bounds = Rect(
        PLACEMENT_INSET,
        PLACEMENT_INSET,
        page_width - 2 * PLACEMENT_INSET,
        page_height - 2 * PLACEMENT_INSET,
    )
    placements = place_labels(stations, bounds)

    # Circles first, so no leader line is drawn over a circle outline.
    c.setLineWidth(0.6)
    c.setStrokeColor(colors.black)
    for _p, s in pairs:
        c.circle(s.x, s.y, s.radius, stroke=1, fill=0)

    c.setFont(LABEL_FONT, font_size)
    c.setFillColor(colors.black)
    cap_height = font_size * LABEL_CAP_HEIGHT_RATIO
    for (p, s), placement in zip(pairs, placements):
        c.setLineWidth(0.4)
        c.line(*placement.leader_from, *placement.leader_to)
        # drawString sits on the baseline, so centre the cap-height band
        # (not the em) inside the rect the placer reserved.
        baseline = placement.rect.y + (placement.rect.height - cap_height) / 2
        c.drawString(placement.rect.x, baseline, p.callsign)

    _draw_titles(c, contest, geo)
    _draw_footer(c, geo)

    c.showPage()
    c.save()
    return buffer.getvalue()

"""Static lookup data for the registration form."""
from __future__ import annotations

from django.utils.translation import gettext_lazy as _

# 26 Swiss cantons, 2-letter ISO 3166-2:CH codes.
SWISS_CANTONS: tuple[tuple[str, str], ...] = (
    ("AG", _("Aargau")),
    ("AI", _("Appenzell Innerrhoden")),
    ("AR", _("Appenzell Ausserrhoden")),
    ("BE", _("Bern")),
    ("BL", _("Basel-Landschaft")),
    ("BS", _("Basel-Stadt")),
    ("FR", _("Fribourg")),
    ("GE", _("Genève")),
    ("GL", _("Glarus")),
    ("GR", _("Graubünden")),
    ("JU", _("Jura")),
    ("LU", _("Luzern")),
    ("NE", _("Neuchâtel")),
    ("NW", _("Nidwalden")),
    ("OW", _("Obwalden")),
    ("SG", _("St. Gallen")),
    ("SH", _("Schaffhausen")),
    ("SO", _("Solothurn")),
    ("SZ", _("Schwyz")),
    ("TG", _("Thurgau")),
    ("TI", _("Ticino")),
    ("UR", _("Uri")),
    ("VD", _("Vaud")),
    ("VS", _("Valais")),
    ("ZG", _("Zug")),
    ("ZH", _("Zürich")),
)

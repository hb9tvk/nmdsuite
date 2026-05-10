"""Registration form for new contest participants."""
from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .callsigns import is_valid_callsign, normalize_callsign
from .constants import SWISS_CANTONS
from .coords import CoordinateError, parse_coordinate_pair


class RegistrationForm(forms.Form):
    """Public registration form.

    The map picker arrives in slice 3. For now the form collects coordinates
    as two text fields and infers the system (WGS84 / CH1903 / CH1903+) from
    the input magnitude — see :mod:`registration.coords`.
    """

    callsign = forms.CharField(
        label=_("Callsign"),
        max_length=20,
        help_text=_("Your contest callsign, with /P if you use it on air."),
    )
    first_name = forms.CharField(label=_("First name"), max_length=80)
    email = forms.EmailField(label=_("Email"))

    multi_op = forms.TypedChoiceField(
        label=_("Multi-operator station"),
        choices=((False, _("No (single operator)")), (True, _("Yes"))),
        coerce=lambda v: v in (True, "True", "true", "1"),
        widget=forms.RadioSelect,
        initial=False,
    )
    station_chief = forms.CharField(
        label=_("Station chief callsign"),
        max_length=20,
        required=False,
        help_text=_("Required only for multi-operator stations."),
    )

    coord_input_e = forms.CharField(
        label=_("Easting / longitude"),
        max_length=32,
        help_text=_("WGS84 e.g. 8.2275 — CH1903 e.g. 660000 — CH1903+ e.g. 2660000"),
    )
    coord_input_n = forms.CharField(
        label=_("Northing / latitude"),
        max_length=32,
        help_text=_("WGS84 e.g. 46.8182 — CH1903 e.g. 190000 — CH1903+ e.g. 1190000"),
    )
    altitude_m = forms.IntegerField(
        label=_("Altitude (m a.s.l.)"),
        min_value=0,
        max_value=5000,
        help_text=_("The contest rules require ≥ 800 m above sea level."),
    )

    canton = forms.ChoiceField(label=_("Canton"), choices=SWISS_CANTONS)

    mode_cw = forms.BooleanField(label=_("CW"), required=False)
    mode_ssb = forms.BooleanField(label=_("SSB"), required=False)

    remarks = forms.CharField(
        label=_("Remarks"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )

    # --- field-level cleaning ---------------------------------------------------------------

    def clean_callsign(self) -> str:
        raw = normalize_callsign(self.cleaned_data["callsign"])
        if not is_valid_callsign(raw):
            raise forms.ValidationError(_("Not a recognizable callsign."))
        return raw

    def clean_station_chief(self) -> str:
        raw = self.cleaned_data.get("station_chief", "")
        if not raw:
            return ""
        normalized = normalize_callsign(raw)
        if not is_valid_callsign(normalized):
            raise forms.ValidationError(_("Not a recognizable callsign."))
        return normalized

    # --- form-level cleaning ----------------------------------------------------------------

    def clean(self):
        cleaned = super().clean()

        if cleaned.get("multi_op") and not cleaned.get("station_chief"):
            self.add_error(
                "station_chief",
                _("Please provide the station chief callsign for multi-operator stations."),
            )

        if not cleaned.get("mode_cw") and not cleaned.get("mode_ssb"):
            # The contest rules require at least one mode.
            self.add_error(
                "mode_cw",
                _("Select at least one operating mode (CW or SSB)."),
            )

        e_input = cleaned.get("coord_input_e")
        n_input = cleaned.get("coord_input_n")
        if e_input and n_input:
            try:
                cleaned["parsed_coords"] = parse_coordinate_pair(e_input, n_input)
            except CoordinateError as exc:
                self.add_error("coord_input_e", str(exc))

        return cleaned

    def operating_modes_value(self) -> int:
        """Encode the two booleans into Participant.Mode (1=CW, 2=SSB, 3=both)."""
        cw = bool(self.cleaned_data.get("mode_cw"))
        ssb = bool(self.cleaned_data.get("mode_ssb"))
        return (1 if cw else 0) | (2 if ssb else 0)

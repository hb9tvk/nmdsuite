"""Registration form for new contest participants."""
from __future__ import annotations

import math

from django import forms
from django.utils.translation import gettext_lazy as _

from .callsigns import is_valid_callsign, login_username, normalize_callsign
from .constants import SWISS_CANTONS
from .coords import CoordinateError, parse_coordinate_pair


# QRB warning: stations closer than this risk receiver overload from a
# neighbour's strong nearby signal. 3 km matches the rule-of-thumb used
# by the contest commission. Permissive — the form lets people register
# anyway after they tick "I'm aware".
QRB_THRESHOLD_M = 3000


class RegistrationForm(forms.Form):
    """Public registration form.

    The map picker arrives in slice 3. For now the form collects coordinates
    as two text fields and infers the system (WGS84 / CH1903 / CH1903+) from
    the input magnitude — see :mod:`registration.coords`.
    """

    callsign = forms.CharField(
        label=_("Callsign"),
        max_length=20,
        help_text=_("Your contest callsign. We strip a trailing /P automatically — the portable suffix is implicit on NMD."),
        widget=forms.TextInput(attrs={"style": "text-transform: uppercase", "oninput": "this.value=this.value.toUpperCase()"}),
    )
    first_name = forms.CharField(
        label=_("First name(s)"),
        max_length=80,
        help_text=_("If multi-operator station: list all first names joined with '+'."),
    )
    email = forms.EmailField(label=_("Email"))

    multi_op = forms.TypedChoiceField(
        label=_("Station type"),
        choices=((False, _("Single-operator station")), (True, _("Multi-operator station"))),
        coerce=lambda v: v in (True, "True", "true", "1"),
        widget=forms.RadioSelect,
        initial=False,
    )
    station_chief = forms.CharField(
        label=_("Station chief callsign"),
        max_length=20,
        required=False,
        help_text=_("Required only for multi-operator stations."),
        widget=forms.TextInput(attrs={"style": "text-transform: uppercase", "oninput": "this.value=this.value.toUpperCase()"}),
    )

    location_text = forms.CharField(
        label=_("Location name"),
        max_length=120,
        help_text=_("Friendly name for your station's location (village, locality, summit name, …)."),
    )
    coord_input_e = forms.CharField(
        label=_("Easting"),
        max_length=32,
        help_text=_("CH1903 e.g. 660000 — also accepts CH1903+ (2660000) or WGS84 (8.2275)"),
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    coord_input_n = forms.CharField(
        label=_("Northing"),
        max_length=32,
        help_text=_("CH1903 e.g. 190000 — also accepts CH1903+ (1190000) or WGS84 (46.8182)"),
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    altitude_m = forms.IntegerField(
        label=_("Altitude (m a.s.l.)"),
        min_value=0,
        max_value=5000,
        help_text=_("Filled automatically from Swisstopo when you pick a location on the map."),
        widget=forms.TextInput(
            attrs={
                "readonly": "readonly",
                "tabindex": "-1",
                "inputmode": "numeric",
                "autocomplete": "off",
            }
        ),
    )

    canton = forms.ChoiceField(
        label=_("Canton"),
        choices=[("", _("— select —"))] + list(SWISS_CANTONS),
        help_text=_("Filled automatically from Swisstopo when you pick a location on the map."),
        # Disable browser autofill — otherwise the browser may repopulate this
        # field from prior submissions on page load, which the JS then mistakes
        # for a manual user override and refuses to overwrite.
        widget=forms.Select(attrs={"autocomplete": "off"}),
    )

    mode_cw = forms.BooleanField(label=_("CW"), required=False)
    mode_ssb = forms.BooleanField(label=_("SSB"), required=False)

    remarks = forms.CharField(
        label=_("Remarks"), required=False, widget=forms.Textarea(attrs={"rows": 3})
    )

    qrb_acknowledged = forms.BooleanField(required=False)

    def __init__(self, *args, contest=None, **kwargs):
        """``contest`` is needed to look up already-registered stations for
        the QRB proximity check. Optional so test helpers that don't pass
        it still work; the check just becomes a no-op."""
        self.contest = contest
        super().__init__(*args, **kwargs)
        # Attribute populated by ``clean()`` when stations < 3 km exist; the
        # template reads it to render the warning banner.
        self.nearby_stations: list[tuple[str, int]] = []

    # --- field-level cleaning ---------------------------------------------------------------

    def clean_callsign(self) -> str:
        raw = normalize_callsign(self.cleaned_data["callsign"])
        if not is_valid_callsign(raw):
            raise forms.ValidationError(_("Not a recognizable callsign."))
        return login_username(raw)

    def clean_station_chief(self) -> str:
        raw = self.cleaned_data.get("station_chief", "")
        if not raw:
            return ""
        normalized = normalize_callsign(raw)
        if not is_valid_callsign(normalized):
            raise forms.ValidationError(_("Not a recognizable callsign."))
        return login_username(normalized)

    def clean_altitude_m(self) -> int:
        v = self.cleaned_data["altitude_m"]
        if v < 800:
            raise forms.ValidationError(
                _("Below 800 m — contest rules require minimum 800 m a.s.l.")
            )
        return v

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
                parsed = parse_coordinate_pair(e_input, n_input)
            except CoordinateError as exc:
                self.add_error("coord_input_e", str(exc))
            else:
                cleaned["parsed_coords"] = parsed
                self.nearby_stations = self._find_nearby_stations(
                    parsed.ch1903p_e, parsed.ch1903p_n,
                )
                if self.nearby_stations and not cleaned.get("qrb_acknowledged"):
                    self.add_error(
                        "qrb_acknowledged",
                        _(
                            "Another station is registered within %(km)s km of your "
                            "chosen location. Please consider moving further away to "
                            "avoid receiver overload, or tick the box to confirm "
                            "you're aware of the conflict."
                        ) % {"km": QRB_THRESHOLD_M // 1000},
                    )

        return cleaned

    def _find_nearby_stations(self, e: float, n: float) -> list[tuple[str, int]]:
        """Return ``[(callsign, distance_m), …]`` for every active participant
        whose LV95 coordinates are within :data:`QRB_THRESHOLD_M` of
        ``(e, n)``, sorted by distance ascending."""
        if self.contest is None:
            return []
        # Local import — keeps the form module's import graph one-way for
        # tests that mock the form or use it without DB access.
        from core.models import Participant
        nearby: list[tuple[str, int]] = []
        qs = (
            Participant.objects
            .filter(contest=self.contest, cancelled_at__isnull=True)
            .exclude(ch1903p_e__isnull=True)
            .exclude(ch1903p_n__isnull=True)
            .values_list("callsign", "ch1903p_e", "ch1903p_n")
        )
        for callsign, pe, pn in qs:
            dist = math.hypot(pe - e, pn - n)
            if dist < QRB_THRESHOLD_M:
                nearby.append((callsign, int(round(dist))))
        nearby.sort(key=lambda x: x[1])
        return nearby

    def operating_modes_value(self) -> int:
        """Encode the two booleans into Participant.Mode (1=CW, 2=SSB, 3=both)."""
        cw = bool(self.cleaned_data.get("mode_cw"))
        ssb = bool(self.cleaned_data.get("mode_ssb"))
        return (1 if cw else 0) | (2 if ssb else 0)

"""Auth and profile forms for the participant portal."""
from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

from registration.callsigns import login_username, normalize_callsign
from registration.forms import RegistrationForm


class CallsignAuthenticationForm(AuthenticationForm):
    """Login form that normalizes the entered callsign before authentication.

    Operators sometimes enter their callsign lower-case, or include the on-air
    ``/P`` suffix that we strip when creating the user account. Apply the same
    normalization here so login accepts whichever form they type.
    """

    def clean_username(self) -> str:
        raw = self.cleaned_data.get("username", "")
        return login_username(normalize_callsign(raw))


# Identity fields that the operator can never change after registration.
_IMMUTABLE_REGISTRATION_FIELDS = ("callsign", "first_name", "email")


class QsoEntryForm(forms.Form):
    """One row in the participant's QSO log.

    Permissive: every field is optional and untyped at the form level — fast
    contest entry shouldn't be interrupted by validation. Visual feedback
    happens client-side (red borders) and per-cell on render. The final
    "submit log" action (M2.5) is what enforces every-row-must-be-valid.

    Mode is derived from RST-sent length at save time (3 = CW, 2 = SSB)
    and persisted on the model.
    """

    utc = forms.CharField(
        label=_("UTC (HHMM)"), max_length=4, required=False,
        widget=forms.TextInput(attrs={"size": "5", "maxlength": "4", "inputmode": "numeric", "autocomplete": "off"}),
    )
    remote_call = forms.CharField(
        label=_("Callsign"), max_length=20, required=False,
        widget=forms.TextInput(attrs={"size": "12", "autocapitalize": "characters", "autocomplete": "off"}),
    )
    rsts = forms.CharField(
        label=_("RST sent"), max_length=3, required=False,
        widget=forms.TextInput(attrs={"size": "4", "maxlength": "3", "inputmode": "numeric", "autocomplete": "off"}),
    )
    txts = forms.CharField(
        label=_("Text sent"), max_length=255, required=False,
        widget=forms.TextInput(attrs={"size": "20", "autocomplete": "off"}),
    )
    rstr = forms.CharField(
        label=_("RST received"), max_length=3, required=False,
        widget=forms.TextInput(attrs={"size": "4", "maxlength": "3", "inputmode": "numeric", "autocomplete": "off"}),
    )
    txtr = forms.CharField(
        label=_("Text received"), max_length=255, required=False,
        widget=forms.TextInput(attrs={"size": "20", "autocomplete": "off"}),
    )


class StationDataForm(RegistrationForm):
    """The unified post-registration edit form.

    Inherits every registration field (with its validators and the
    coordinate-detection logic in ``RegistrationForm.clean``), drops
    the three identity fields the operator can never change, and adds
    the equipment-side fields (operator name, output power, 11
    component slots).

    Single form, single save path, single template — no more "two
    forms for what's logically one record".
    """

    def __init__(self, *args, **kwargs):
        # Local import dodges the circular dep portal.forms ↔ portal.station_service.
        from .station_service import COMPONENT_LABELS

        super().__init__(*args, **kwargs)
        for name in _IMMUTABLE_REGISTRATION_FIELDS:
            self.fields.pop(name, None)

        # Equipment fields after the registration ones in iteration order.
        self.fields["op_name"] = forms.CharField(
            label=_("Operator (first and last name)"),
            max_length=80,
            required=False,
        )
        self.fields["watt"] = forms.CharField(
            label=_("Output power"), max_length=20, required=False,
        )
        self._component_labels = COMPONENT_LABELS
        for i, label in enumerate(COMPONENT_LABELS, start=1):
            self.fields[f"sta{i:02d}bez"] = forms.CharField(
                label=label,
                max_length=120,
                required=False,
            )
            self.fields[f"sta{i:02d}gramm"] = forms.IntegerField(
                label=_("Weight (g)"),
                min_value=0,
                max_value=20_000,
                required=False,
                widget=forms.NumberInput(attrs={"inputmode": "numeric", "step": "1", "class": "station-weight-input"}),
            )

    def component_rows(self):
        """Yield (label, description_field, weight_field) for the template."""
        for i, label in enumerate(self._component_labels, start=1):
            yield label, self[f"sta{i:02d}bez"], self[f"sta{i:02d}gramm"]

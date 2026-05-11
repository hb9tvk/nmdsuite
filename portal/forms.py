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


class ProfileEditForm(RegistrationForm):
    """Profile-edit form for the participant portal.

    Same validators and UX as the public registration form, minus the three
    immutable identity fields (callsign, first name, email) — which the spec
    explicitly forbids changing after signup. Their current values are shown
    as plain text in the template instead.
    """

    IMMUTABLE_FIELDS = ("callsign", "first_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.IMMUTABLE_FIELDS:
            self.fields.pop(name, None)


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

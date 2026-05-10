"""Auth and profile forms for the participant portal."""
from __future__ import annotations

from django.contrib.auth.forms import AuthenticationForm

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

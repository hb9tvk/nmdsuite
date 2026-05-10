"""Auth forms for the participant portal."""
from __future__ import annotations

from django.contrib.auth.forms import AuthenticationForm

from registration.callsigns import login_username, normalize_callsign


class CallsignAuthenticationForm(AuthenticationForm):
    """Login form that normalizes the entered callsign before authentication.

    Operators sometimes enter their callsign lower-case, or include the on-air
    ``/P`` suffix that we strip when creating the user account. Apply the same
    normalization here so login accepts whichever form they type.
    """

    def clean_username(self) -> str:
        raw = self.cleaned_data.get("username", "")
        return login_username(normalize_callsign(raw))

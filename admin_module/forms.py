"""Admin-module forms."""
from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _


class BulkEmailForm(forms.Form):
    """Compose-a-message form for the M4.4 bulk-email surface.

    Plain text; the body supports two per-recipient placeholders
    (``{callsign}``, ``{first_name}``) — see
    :mod:`admin_module.email_service`.
    """

    subject = forms.CharField(
        label=_("Subject"),
        max_length=200,
        widget=forms.TextInput(attrs={"autocomplete": "off", "size": "60"}),
    )
    body = forms.CharField(
        label=_("Message"),
        widget=forms.Textarea(attrs={"rows": 14, "cols": 60}),
    )

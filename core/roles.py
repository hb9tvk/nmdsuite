"""Non-staff admin roles.

The **Redaktion** group grants a club-magazine editor access to the
publication-prep tools (participant reports, ranking preview, ranking PDF)
*without* full staff privileges. Members are created with ``is_staff=False``,
so every ``is_staff``-gated admin view stays closed to them — the editor
tools opt in explicitly via :func:`core.roles.is_editor`.

The group is created by a data migration (``core/migrations/0014_*``); a
superuser adds/removes members from it in the Django admin. Membership
survives the annual ``setup_new_contest`` reset (which only deactivates
plain participant accounts).
"""
from __future__ import annotations

REDAKTION_GROUP = "Redaktion"


def is_editor(user) -> bool:
    """True if ``user`` is an active Redaktion-group member.

    Independent of ``is_staff``: an editor is deliberately *not* staff, so
    this is the only thing that opens the publication-prep tools to them.
    Full staff get those tools through the normal staff gate, not here.
    """
    return bool(
        getattr(user, "is_authenticated", False)
        and user.is_active
        and user.groups.filter(name=REDAKTION_GROUP).exists()
    )

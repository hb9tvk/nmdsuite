"""Template context processors that surface the active contest in every page."""
from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from .models import Contest
from .roles import is_editor


def active_contest(request: HttpRequest) -> dict[str, Any]:
    contest = (
        Contest.objects.exclude(state=Contest.State.ARCHIVED)
        .order_by("-year")
        .first()
    )
    return {"active_contest": contest}


def user_roles(request: HttpRequest) -> dict[str, Any]:
    """Expose the Redaktion-editor flag to templates (nav link gating).
    ``is_staff`` is already provided by the auth context processor."""
    return {"is_editor": is_editor(request.user)}

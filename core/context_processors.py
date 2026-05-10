"""Template context processors that surface the active contest in every page."""
from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from .models import Contest


def active_contest(request: HttpRequest) -> dict[str, Any]:
    contest = (
        Contest.objects.exclude(state=Contest.State.ARCHIVED)
        .order_by("-year")
        .first()
    )
    return {"active_contest": contest}

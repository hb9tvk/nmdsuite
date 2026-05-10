"""Helpers for any view that embeds the registration map picker."""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest


def map_picker_context(request: HttpRequest) -> dict[str, Any]:
    """Context entries the map-picker partials need."""
    return {
        "swisstopo_height_api": settings.SWISSTOPO_HEIGHT_API,
        "swisstopo_identify_api": settings.SWISSTOPO_IDENTIFY_API,
    }

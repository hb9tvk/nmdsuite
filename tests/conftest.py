"""Pytest fixtures shared across the test suite."""
from __future__ import annotations

import pytest


@pytest.fixture
def seeded_contest(db):
    """A 2026 contest row seeded via the management command."""
    from django.core.management import call_command

    call_command("seed_contest", "--year", "2026")
    from core.models import Contest

    return Contest.objects.get(year=2026)


@pytest.fixture
def participant_user(db):
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    user = user_model.objects.create_user(username="HB9TEST", password="strong-pass-1234", email="test@example.com")
    return user

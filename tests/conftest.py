"""Pytest fixtures shared across the test suite."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def english_for_tests(settings):
    """Pin every test to the English locale.

    The project defaults to German (``LANGUAGE_CODE = "de"``), and after M5
    the .po files actually contain translations — so response bodies
    render in German. Tests assert against the English msgid strings
    (the source-code form), so we force ``LANGUAGE_CODE = "en"`` here.
    Browser-driven Playwright tests, if any, would need their own locale
    handling.
    """
    settings.LANGUAGE_CODE = "en"


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

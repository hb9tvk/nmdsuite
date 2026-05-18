"""Registration service layer.

Handles account creation and the registration-vs-existing-account logic
described in the project's account-lifecycle decision: persistent accounts
across years, password unchanged on re-registration.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from core.audit import audit
from core.models import Contest, Participant

from .callsigns import login_username, normalize_callsign

User = get_user_model()


@dataclass
class RegistrationOutcome:
    participant: Participant
    user_was_created: bool
    generated_password: str | None  # None when reusing an existing account


def _generate_password(length: int = 12) -> str:
    """URL-safe random password. ``secrets.token_urlsafe`` returns ~1.3 chars per byte."""
    return secrets.token_urlsafe(length)[:length]


@transaction.atomic
def register_participant(
    *, contest: Contest, form_data: dict[str, Any], actor: User | None = None,
) -> RegistrationOutcome:
    """Persist a registration and the user account that goes with it.

    ``form_data`` is what ``RegistrationForm.cleaned_data`` produces, plus a
    pre-computed ``operating_modes`` int.

    ``actor`` is the user attributed in the audit log. Defaults to the
    participant's own user (self-service registration). When admin staff
    register on behalf of someone else (M4.3), the staff user is passed in
    and an ``on_behalf=True`` flag is recorded in the audit payload.

    Raises ``Participant.MultipleObjectsReturned`` etc. only on programmer
    error; duplicate-registration-for-active-contest is enforced via the
    ``UniqueConstraint`` on ``(contest, user)`` and surfaced as IntegrityError.
    """
    callsign = normalize_callsign(form_data["callsign"])
    username = login_username(callsign)

    user = User.objects.filter(username=username).first()
    user_was_created = False
    generated_password: str | None = None

    if user is None:
        generated_password = _generate_password()
        user = User.objects.create_user(
            username=username,
            email=form_data["email"],
            password=generated_password,
            first_name=form_data["first_name"],
        )
        user_was_created = True
    else:
        # Returning operator. Per project policy: do not reset their password,
        # but refresh the contact details so they get this year's emails.
        if user.email != form_data["email"]:
            user.email = form_data["email"]
        if user.first_name != form_data["first_name"]:
            user.first_name = form_data["first_name"]
        if not user.is_active:
            user.is_active = True
        user.save()

    parsed = form_data["parsed_coords"]

    participant = Participant.objects.create(
        contest=contest,
        user=user,
        callsign=callsign,
        first_name=form_data["first_name"],
        email=form_data["email"],
        multi_op=bool(form_data["multi_op"]),
        station_chief=form_data.get("station_chief", "") or "",
        coord_system_input=parsed.detected_system,
        coord_input_e=form_data["coord_input_e"],
        coord_input_n=form_data["coord_input_n"],
        ch1903p_e=parsed.ch1903p_e,
        ch1903p_n=parsed.ch1903p_n,
        wgs84_lat=parsed.wgs84_lat,
        wgs84_lon=parsed.wgs84_lon,
        altitude_m=form_data["altitude_m"],
        canton=form_data["canton"],
        location_text=(form_data.get("location_text", "") or "").strip(),
        operating_modes=form_data["operating_modes"],
        remarks=form_data.get("remarks", "") or "",
    )

    audit_actor = actor or user
    audit_payload = {
        "user_was_created": user_was_created,
        "multi_op": participant.multi_op,
        "canton": participant.canton,
    }
    if actor is not None and actor != user:
        audit_payload["on_behalf"] = True
    audit(
        action="registration.create",
        actor=audit_actor,
        target=callsign,
        contest=contest,
        payload=audit_payload,
    )

    return RegistrationOutcome(
        participant=participant,
        user_was_created=user_was_created,
        generated_password=generated_password,
    )


def cancel_participation(participant: Participant, *, actor: User | None = None) -> None:
    """Mark a participant cancelled. The user account is *not* deleted —
    the operator may re-register later or have data from prior years."""
    participant.cancelled_at = timezone.now()
    participant.save(update_fields=["cancelled_at"])
    audit(
        action="registration.cancel",
        actor=actor or participant.user,
        target=participant.callsign,
        contest=participant.contest,
    )



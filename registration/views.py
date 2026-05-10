"""Public registration views."""
from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from core.models import Contest, Participant

from .emails import send_registration_confirmation
from .forms import RegistrationForm
from .services import register_participant


def _active_contest() -> Contest | None:
    return (
        Contest.objects.exclude(state=Contest.State.ARCHIVED)
        .order_by("-year")
        .first()
    )


def index(request):
    contest = _active_contest()
    if contest is None:
        return render(request, "registration/closed.html", {"reason": "no_contest"})

    if contest.state != Contest.State.REGISTRATION_OPEN:
        return render(request, "registration/closed.html", {"reason": "state", "contest": contest})

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            payload = dict(form.cleaned_data)
            payload["operating_modes"] = form.operating_modes_value()
            try:
                outcome = register_participant(contest=contest, form_data=payload)
            except IntegrityError:
                messages.error(
                    request,
                    _("This callsign is already registered for the current contest."),
                )
            else:
                send_registration_confirmation(
                    participant=outcome.participant,
                    contest=contest,
                    generated_password=outcome.generated_password,
                )
                request.session["registration_success_callsign"] = outcome.participant.callsign
                request.session["registration_user_was_created"] = outcome.user_was_created
                return redirect(reverse("registration:success"))
    else:
        form = RegistrationForm()

    from core.picker import map_picker_context

    return render(
        request,
        "registration/index.html",
        {
            "form": form,
            "contest": contest,
            "registrations_url": reverse("registration:registrations_json"),
            **map_picker_context(request),
        },
    )


def success(request):
    callsign = request.session.pop("registration_success_callsign", None)
    user_was_created = request.session.pop("registration_user_was_created", None)
    if callsign is None:
        return redirect(reverse("registration:index"))
    return render(
        request,
        "registration/success.html",
        {"callsign": callsign, "user_was_created": user_was_created},
    )


@require_GET
def registrations_json(request):
    """Return active-contest participants as map markers.

    Public endpoint — operators see each other's locations on the registration
    map so they can avoid clustering. Only the callsign and coordinates are
    exposed; emails / station details stay private.
    """
    contest = _active_contest()
    if contest is None:
        return JsonResponse({"contest": None, "participants": []})

    qs = (
        Participant.objects
        .filter(contest=contest, cancelled_at__isnull=True)
        .exclude(wgs84_lat__isnull=True)
        .exclude(wgs84_lon__isnull=True)
        .values("callsign", "wgs84_lat", "wgs84_lon", "altitude_m", "canton")
    )
    return JsonResponse({
        "contest": contest.year,
        "participants": [
            {
                "callsign": p["callsign"],
                "lat": p["wgs84_lat"],
                "lon": p["wgs84_lon"],
                "altitude_m": p["altitude_m"],
                "canton": p["canton"],
            }
            for p in qs
        ],
    })

"""Participant portal views (M2.1: dashboard + profile edit + cancel).

The login / logout / password-reset views live directly in ``portal.urls``
via the Django built-ins, so they aren't repeated here.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from core.models import Contest, Participant
from core.picker import map_picker_context
from registration.services import cancel_participation, update_participant_profile

from .forms import ProfileEditForm


def _active_contest() -> Contest | None:
    return (
        Contest.objects.exclude(state=Contest.State.ARCHIVED)
        .order_by("-year")
        .first()
    )


def _active_participation(user, contest: Contest | None) -> Participant | None:
    """Find the user's non-cancelled participation in ``contest``."""
    if contest is None:
        return None
    return (
        Participant.objects
        .filter(contest=contest, user=user, cancelled_at__isnull=True)
        .first()
    )


@login_required
def dashboard(request):
    contest = _active_contest()
    participant = _active_participation(request.user, contest)
    return render(
        request,
        "portal/dashboard.html",
        {
            "contest": contest,
            "participant": participant,
            "registration_open": contest is not None and contest.state == Contest.State.REGISTRATION_OPEN,
        },
    )


# --- profile edit ----------------------------------------------------------------------------


def _profile_initial(participant: Participant) -> dict:
    return {
        "multi_op": participant.multi_op,
        "station_chief": participant.station_chief,
        "coord_input_e": participant.coord_input_e,
        "coord_input_n": participant.coord_input_n,
        "altitude_m": participant.altitude_m,
        "canton": participant.canton,
        "mode_cw": bool(participant.operating_modes & 1),
        "mode_ssb": bool(participant.operating_modes & 2),
        "remarks": participant.remarks,
    }


@login_required
def edit_profile(request):
    contest = _active_contest()
    participant = _active_participation(request.user, contest)
    if participant is None:
        messages.info(request, _("You are not registered for the current contest."))
        return redirect("portal:dashboard")

    if request.method == "POST":
        form = ProfileEditForm(request.POST)
        if form.is_valid():
            payload = dict(form.cleaned_data)
            payload["operating_modes"] = form.operating_modes_value()
            update_participant_profile(participant=participant, form_data=payload)
            messages.success(request, _("Your registration data has been updated."))
            return redirect("portal:dashboard")
    else:
        form = ProfileEditForm(initial=_profile_initial(participant))

    return render(
        request,
        "portal/edit_profile.html",
        {
            "form": form,
            "participant": participant,
            "contest": contest,
            "registrations_url": reverse("registration:registrations_json"),
            **map_picker_context(request),
        },
    )


# --- cancel participation --------------------------------------------------------------------


@login_required
def cancel(request):
    contest = _active_contest()
    participant = _active_participation(request.user, contest)
    if participant is None:
        messages.info(request, _("You are not registered for the current contest."))
        return redirect("portal:dashboard")

    if request.method == "POST":
        cancel_participation(participant, actor=request.user)
        logout(request)
        messages.success(
            request,
            _("Your participation has been cancelled. You can register again any time before the contest."),
        )
        return redirect("portal:login")

    return render(request, "portal/cancel.html", {"participant": participant, "contest": contest})

"""Public registration views."""
from __future__ import annotations

from django.contrib import messages
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from core.models import Contest

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

    return render(
        request,
        "registration/index.html",
        {"form": form, "contest": contest},
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

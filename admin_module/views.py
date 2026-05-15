"""Administration module — staff-only dashboard + audit log (M4.1).

This is the workflow UI for contest staff. Low-level data inspection
still lives at ``/django-admin/`` (the Django contrib admin); the admin
module here is the curated, workflow-driven surface.

M4.1 scope: a landing page that summarises the active contest plus an
``AuditLog`` viewer with filtering. Later M4 slices add the
state-machine transitions (close registration / close logs / publish /
setup-new), on-behalf editing, bulk email, and backup/restore.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from core.models import AuditLog, Contest, Participant
from core.picker import map_picker_context
from portal.forms import ProfileEditForm
from registration.forms import RegistrationForm
from registration.services import register_participant, update_participant_profile

from . import services


def _staff_required(view):
    return login_required(user_passes_test(lambda u: u.is_staff)(view))


def _active_contest() -> Contest | None:
    return (
        Contest.objects
        .exclude(state=Contest.State.ARCHIVED)
        .order_by("-year")
        .first()
    )


@_staff_required
def index(request):
    contest = _active_contest()
    counts = {"registered": 0, "active": 0, "cancelled": 0, "submitted": 0, "pending": 0}
    if contest is not None:
        qs = Participant.objects.filter(contest=contest)
        counts["registered"] = qs.count()
        counts["cancelled"] = qs.filter(cancelled_at__isnull=False).count()
        active = qs.filter(cancelled_at__isnull=True)
        counts["active"] = active.count()
        counts["submitted"] = active.filter(submitted_at__isnull=False).count()
        counts["pending"] = active.filter(submitted_at__isnull=True).count()

    recent_audit = AuditLog.objects.select_related("actor").order_by("-timestamp")[:20]
    return render(request, "admin_module/index.html", {
        "contest": contest,
        "counts": counts,
        "recent_audit": recent_audit,
    })


# --- contest state transitions (M4.2) ---------------------------------------------------------


def _run_transition(request, transition, *, success_msg, **kwargs):
    """Common wrapper: call ``transition(contest, actor=...)`` on the
    active contest, surface success or TransitionError as a flash, and
    redirect back to the dashboard."""
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")
    try:
        result = transition(contest, actor=request.user, **kwargs)
    except services.TransitionError as exc:
        messages.error(request, str(exc))
        return redirect("admin_module:index")
    messages.success(request, success_msg(result) if callable(success_msg) else success_msg)
    return redirect("admin_module:index")


@_staff_required
@require_http_methods(["POST"])
def close_registration(request):
    return _run_transition(
        request, services.close_registration,
        success_msg=_("Registration closed."),
    )


@_staff_required
@require_http_methods(["POST"])
def open_log_submission(request):
    return _run_transition(
        request, services.open_log_submission,
        success_msg=_("Log submission opened."),
    )


@_staff_required
@require_http_methods(["POST"])
def close_log_submission(request):
    return _run_transition(
        request, services.close_log_submission,
        success_msg=lambda n: _("Log submission closed; %(n)d pending logs were auto-submitted.") % {"n": n},
    )


@_staff_required
@require_http_methods(["POST"])
def publish_results(request):
    return _run_transition(
        request, services.publish_results,
        success_msg=_("Results published."),
    )


@_staff_required
@require_http_methods(["POST"])
def revert_state(request):
    """Single 'go back one step' endpoint. Dispatches to the right
    reverse service based on the contest's current state. No-op (with
    an error flash) if there's nothing to revert."""
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")
    reverters = {
        Contest.State.REGISTRATION_CLOSED: (services.revert_close_registration, _("Registration reopened.")),
        Contest.State.LOGS_OPEN: (services.revert_open_log_submission, _("Reverted to registration closed.")),
        Contest.State.LOGS_CLOSED: (
            services.revert_close_log_submission,
            lambda n: _("Log submission reopened; %(n)d auto-submitted logs unlocked.") % {"n": n},
        ),
        Contest.State.PUBLISHED: (services.revert_publish_results, _("Results unpublished.")),
    }
    entry = reverters.get(contest.state)
    if entry is None:
        messages.error(request, _("Nothing to revert from this state."))
        return redirect("admin_module:index")
    fn, msg = entry
    try:
        result = fn(contest, actor=request.user)
    except services.TransitionError as exc:
        messages.error(request, str(exc))
        return redirect("admin_module:index")
    messages.success(request, msg(result) if callable(msg) else msg)
    return redirect("admin_module:index")


@_staff_required
@require_http_methods(["POST"])
def setup_new_contest(request):
    """Archive current contest(s), deactivate non-staff accounts, create a new
    Contest row. POST-only; year comes from the form."""
    raw_year = (request.POST.get("year") or "").strip()
    try:
        year = int(raw_year)
    except ValueError:
        messages.error(request, _("Invalid year."))
        return redirect("admin_module:index")
    try:
        contest = services.setup_new_contest(year=year, actor=request.user)
    except services.TransitionError as exc:
        messages.error(request, str(exc))
        return redirect("admin_module:index")
    messages.success(
        request,
        _("Archived previous contests and deactivated participant accounts. NMD %(y)d seeded.") % {"y": contest.year},
    )
    return redirect("admin_module:index")


# --- on-behalf participant management (M4.3a) -------------------------------------------------
#
# Staff surface for the cross-participant work that the participant portal
# only exposes to the operator themselves: register someone who didn't use
# the public form, fix a typo in a participant's registration data, etc.
# The same forms and service functions back the portal flows; here we just
# select the participant by callsign and attribute the audit row to the
# staff user (with ``on_behalf=True`` in the payload when the actor differs
# from the participant's own user account).


def _participant_or_404(contest: Contest, pk: int) -> Participant:
    """Look up a participant in the active contest by pk.

    Pk-keyed URLs avoid the slash-in-callsign problem (``HB9TVK/P``); the
    callsign is shown for humans in the page chrome.
    """
    try:
        return Participant.objects.select_related("user", "contest").get(
            contest=contest, pk=pk,
        )
    except Participant.DoesNotExist as exc:
        raise Http404("No such participant in the active contest.") from exc


def _status_label(p: Participant) -> str:
    if p.cancelled_at is not None:
        return "cancelled"
    if p.submitted_at is not None:
        return "submitted"
    return "pending"


@_staff_required
def participants_index(request):
    """Sortable/filterable participant table for the active contest."""
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")

    qs = Participant.objects.filter(contest=contest).select_related("user")

    callsign_q = (request.GET.get("callsign") or "").strip()
    if callsign_q:
        qs = qs.filter(callsign__icontains=callsign_q.upper())

    status = (request.GET.get("status") or "").strip()
    if status == "submitted":
        qs = qs.filter(cancelled_at__isnull=True, submitted_at__isnull=False)
    elif status == "pending":
        qs = qs.filter(cancelled_at__isnull=True, submitted_at__isnull=True)
    elif status == "cancelled":
        qs = qs.filter(cancelled_at__isnull=False)

    qs = qs.order_by("callsign")

    rows = [
        {
            "participant": p,
            "status": _status_label(p),
        }
        for p in qs
    ]

    return render(
        request,
        "admin_module/participants_index.html",
        {
            "contest": contest,
            "rows": rows,
            "callsign_q": callsign_q,
            "selected_status": status,
            "total": len(rows),
        },
    )


@_staff_required
def participant_detail(request, pk: int):
    """Per-participant landing page — hub for all on-behalf actions."""
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")
    participant = _participant_or_404(contest, pk)

    qso_count = participant.qsos.count()
    station = getattr(participant, "station", None)

    return render(
        request,
        "admin_module/participant_detail.html",
        {
            "contest": contest,
            "participant": participant,
            "status": _status_label(participant),
            "qso_count": qso_count,
            "station": station,
        },
    )


@_staff_required
def participant_register(request):
    """On-behalf registration. Same form as the public surface, but the
    contest state check is bypassed so staff can register late entries even
    after registration has been closed.
    """
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            payload = dict(form.cleaned_data)
            payload["operating_modes"] = form.operating_modes_value()
            try:
                outcome = register_participant(
                    contest=contest, form_data=payload, actor=request.user,
                )
            except IntegrityError:
                messages.error(
                    request,
                    _("This callsign is already registered for the current contest."),
                )
            else:
                messages.success(
                    request,
                    _("Registered %(call)s on behalf.") % {"call": outcome.participant.callsign},
                )
                return redirect(
                    "admin_module:participant_detail", pk=outcome.participant.pk,
                )
    else:
        form = RegistrationForm()

    return render(
        request,
        "admin_module/participant_register.html",
        {
            "form": form,
            "contest": contest,
            "registrations_url": reverse("registration:registrations_json"),
            **map_picker_context(request),
        },
    )


def _profile_initial(participant: Participant) -> dict:
    """Mirror of portal.views._profile_initial — kept local so the admin view
    doesn't reach into the portal view module."""
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


@_staff_required
def participant_edit_profile(request, pk: int):
    """On-behalf edit of a participant's registration data.

    The portal lock on ``submitted_at`` is intentionally NOT enforced here —
    this view exists so staff can correct mistakes after the operator has
    already submitted.
    """
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")
    participant = _participant_or_404(contest, pk)

    if request.method == "POST":
        form = ProfileEditForm(request.POST)
        if form.is_valid():
            payload = dict(form.cleaned_data)
            payload["operating_modes"] = form.operating_modes_value()
            update_participant_profile(
                participant=participant, form_data=payload, actor=request.user,
            )
            messages.success(
                request,
                _("Updated registration data for %(call)s.") % {"call": participant.callsign},
            )
            return redirect("admin_module:participant_detail", pk=participant.pk)
    else:
        form = ProfileEditForm(initial=_profile_initial(participant))

    return render(
        request,
        "admin_module/participant_edit_profile.html",
        {
            "form": form,
            "participant": participant,
            "contest": contest,
            "registrations_url": reverse("registration:registrations_json"),
            **map_picker_context(request),
        },
    )


@_staff_required
def audit_log(request):
    qs = AuditLog.objects.select_related("actor", "contest").order_by("-timestamp")

    action = (request.GET.get("action") or "").strip()
    if action:
        qs = qs.filter(action=action)
    actor = (request.GET.get("actor") or "").strip()
    if actor:
        qs = qs.filter(actor__username__iexact=actor)
    target = (request.GET.get("target") or "").strip()
    if target:
        qs = qs.filter(target__icontains=target)

    # All distinct action labels for the filter dropdown.
    actions = list(AuditLog.objects.values_list("action", flat=True).distinct().order_by("action"))

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    return render(request, "admin_module/audit_log.html", {
        "page_obj": page_obj,
        "actions": actions,
        "selected_action": action,
        "selected_actor": actor,
        "selected_target": target,
    })

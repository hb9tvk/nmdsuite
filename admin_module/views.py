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
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from core.models import AuditLog, Contest, Participant

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

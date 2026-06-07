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
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from core.audit import audit
from core.models import AuditLog, Contest, Participant, QsoEntry
from core.picker import map_picker_context
from portal import qso_service, station_service, submit_service
from portal.forms import QsoEntryForm, StationDataForm
from portal.qso_upload import UploadParseError, parse_upload
from public.ranking_service import (
    ANTENNA_LABEL,
    PSU_LABEL,
    TRX_LABEL,
    build_ranking_page,
)
from registration.callsigns import normalize_callsign
from registration.forms import RegistrationForm
from registration.services import register_participant

from . import backup_service, email_service, fixstation_service, services
from .forms import BulkEmailForm


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
def rescore(request):
    """Manual Re-run scoring button. Available once logs have closed —
    handy after on-behalf edits or for any reason the admin wants a
    fresh pass without going through Fixstation Review."""
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")
    if contest.state not in (
        Contest.State.LOGS_CLOSED,
        Contest.State.SCORED,
        Contest.State.PUBLISHED,
    ):
        messages.error(
            request,
            _("Re-run scoring is only available after log submission has closed."),
        )
        return redirect("admin_module:index")
    summary = services.rescore_contest(contest, actor=request.user, source="manual")
    total = sum(summary.values())
    messages.success(
        request,
        _("Scoring re-run. %(n)d QSO records updated.") % {"n": total},
    )
    return redirect("admin_module:index")


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

    return render(
        request,
        "admin_module/participant_detail.html",
        {
            "contest": contest,
            "participant": participant,
            "status": _status_label(participant),
            "qso_count": qso_count,
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




# --- on-behalf log + station + submit (M4.3b) -------------------------------------------------
#
# These views mirror the participant portal flows (log entry, station
# description, submit) for a chosen participant. The portal partials are
# reused; URL endpoints come from context so the same partials work for
# both surfaces. The submitted_at lock that gates the portal does NOT
# apply here — admin acts as a release valve when an operator needs help
# after submission.


def _admin_qso_app_context(participant: Participant) -> dict[str, str]:
    """URL endpoints the QSO partials need, pointing at the admin
    on-behalf endpoints scoped to a participant pk."""
    return {
        "qso_save_url": reverse("admin_module:participant_qso_save", kwargs={"pk": participant.pk}),
        "qso_list_url": reverse("admin_module:participant_log_entry", kwargs={"pk": participant.pk}),
    }


def _attach_admin_qso_urls(qsos, participant: Participant):
    for q in qsos:
        q.edit_url = reverse(
            "admin_module:participant_qso_edit",
            kwargs={"pk": participant.pk, "qso_pk": q.pk},
        )
        q.delete_url = reverse(
            "admin_module:participant_qso_delete",
            kwargs={"pk": participant.pk, "qso_pk": q.pk},
        )
    return qsos


def _participant_qso_or_404(participant: Participant, qso_pk: int) -> QsoEntry:
    qso = get_object_or_404(QsoEntry, pk=qso_pk)
    if qso.participant_id != participant.id:
        raise Http404
    return qso


def _raw_qso_data(request) -> dict[str, str]:
    return {
        "utc": request.POST.get("utc", ""),
        "remote_call": request.POST.get("remote_call", ""),
        "rsts": request.POST.get("rsts", ""),
        "txts": request.POST.get("txts", ""),
        "rstr": request.POST.get("rstr", ""),
        "txtr": request.POST.get("txtr", ""),
    }


def _render_qso_app(request, participant: Participant, *, form=None, editing_id=""):
    """Server-rendered QSO app fragment for htmx swaps."""
    qsos = _attach_admin_qso_urls(
        qso_service.list_qsos_with_warnings(participant), participant,
    )
    return render(
        request,
        "portal/_qso_app.html",
        {
            "form": form or QsoEntryForm(),
            "editing_id": editing_id,
            "qsos": qsos,
            # No portal 'locked' flag: admin can edit even after submission.
            "locked": False,
            **_admin_qso_app_context(participant),
        },
    )


@_staff_required
def participant_station(request, pk: int):
    """On-behalf edit of a participant's station data. Mirrors the
    portal flow but with breadcrumb navigation back to the detail page."""
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")
    participant = _participant_or_404(contest, pk)

    if request.method == "POST":
        form = StationDataForm(request.POST)
        if form.is_valid():
            payload = dict(form.cleaned_data)
            payload["operating_modes"] = form.operating_modes_value()
            station_service.save_station(
                participant=participant, data=payload, actor=request.user,
            )
            messages.success(
                request, _("Station data updated for %(call)s.") % {"call": participant.callsign},
            )
            return redirect("admin_module:participant_station", pk=participant.pk)
    else:
        form = StationDataForm(
            initial=station_service.initial_from_participant(participant),
        )

    return render(
        request,
        "admin_module/participant_station.html",
        {
            "form": form,
            "participant": participant,
            "contest": contest,
            "registrations_url": reverse("registration:registrations_json"),
            **map_picker_context(request),
        },
    )


@_staff_required
def participant_log_entry(request, pk: int):
    """List + permissive editing of a participant's QSO log. Uses the
    portal's htmx-driven entry UI."""
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")
    participant = _participant_or_404(contest, pk)

    qsos = _attach_admin_qso_urls(
        qso_service.list_qsos_with_warnings(participant), participant,
    )
    return render(
        request,
        "admin_module/participant_log_entry.html",
        {
            "participant": participant,
            "contest": contest,
            "qsos": qsos,
            "form": QsoEntryForm(),
            "editing_id": "",
            "locked": False,
            **_admin_qso_app_context(participant),
        },
    )


@_staff_required
@require_http_methods(["POST"])
def participant_qso_save(request, pk: int):
    contest = _active_contest()
    if contest is None:
        return redirect("admin_module:index")
    participant = _participant_or_404(contest, pk)

    data = _raw_qso_data(request)
    if not qso_service.is_all_empty(data):
        editing_id = (request.POST.get("editing_id") or "").strip()
        if editing_id:
            qso = _participant_qso_or_404(participant, int(editing_id))
            qso_service.update_qso(qso=qso, data=data)
        else:
            qso_service.create_qso(participant=participant, data=data)
    return _render_qso_app(request, participant)


@_staff_required
def participant_qso_edit(request, pk: int, qso_pk: int):
    contest = _active_contest()
    if contest is None:
        return redirect("admin_module:index")
    participant = _participant_or_404(contest, pk)
    qso = _participant_qso_or_404(participant, qso_pk)
    form = QsoEntryForm(initial=qso_service.initial_from_qso(qso))
    return _render_qso_app(request, participant, form=form, editing_id=qso.pk)


@_staff_required
@require_http_methods(["DELETE", "POST"])
def participant_qso_delete(request, pk: int, qso_pk: int):
    contest = _active_contest()
    if contest is None:
        return redirect("admin_module:index")
    participant = _participant_or_404(contest, pk)
    qso = _participant_qso_or_404(participant, qso_pk)
    qso.delete()
    return _render_qso_app(request, participant)


@_staff_required
@require_http_methods(["POST"])
def participant_qso_upload(request, pk: int):
    """Replace the participant's QSO log + station info from a .nmd/.csv upload."""
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")
    participant = _participant_or_404(contest, pk)

    uploaded = request.FILES.get("file")
    if uploaded is None:
        messages.error(request, _("Please choose a file to upload."))
        return redirect("admin_module:participant_log_entry", pk=participant.pk)

    name = uploaded.name.lower()
    if not (name.endswith(".csv") or name.endswith(".nmd")):
        messages.error(request, _("Only .csv and .nmd files are supported."))
        return redirect("admin_module:participant_log_entry", pk=participant.pk)

    try:
        parsed = parse_upload(uploaded.read())
    except UploadParseError as exc:
        messages.error(request, str(exc))
        return redirect("admin_module:participant_log_entry", pk=participant.pk)

    count = qso_service.replace_qsos_from_upload(
        participant=participant, rows=parsed.qsos, filename=uploaded.name, actor=request.user,
    )
    outcome = station_service.apply_upload_station_info(
        participant, parsed.station_info.fields, actor=request.user,
    )
    parts = [_("Imported %(count)d QSO entries from %(name)s.") % {"count": count, "name": uploaded.name}]
    if outcome.station_updated:
        parts.append(_("Station description updated."))
    if outcome.location_updated:
        parts.append(_("Location updated from the file; altitude and canton refreshed from Swisstopo."))
    messages.success(request, " ".join(str(p) for p in parts))
    if outcome.location_invalid:
        messages.warning(
            request,
            _("The coordinates in the file are outside Switzerland or could not be parsed — the registered location was kept."),
        )
    return redirect("admin_module:participant_log_entry", pk=participant.pk)


@_staff_required
@require_http_methods(["POST"])
def participant_submit(request, pk: int):
    """Force-submit a participant's log on their behalf.

    No confirmation email is sent (the operator did not trigger this);
    admin can follow up manually if needed.
    """
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")
    participant = _participant_or_404(contest, pk)

    if participant.submitted_at is not None:
        messages.info(request, _("Already submitted; nothing to do."))
    else:
        submit_service.submit_log(participant=participant, actor=request.user)
        messages.success(
            request, _("Submitted log on behalf of %(call)s.") % {"call": participant.callsign},
        )
    return redirect("admin_module:participant_detail", pk=participant.pk)


@_staff_required
@require_http_methods(["POST"])
def participant_release(request, pk: int):
    """Un-submit a previously-submitted log so the operator can edit again."""
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")
    participant = _participant_or_404(contest, pk)

    if participant.submitted_at is None:
        messages.info(request, _("Not currently submitted; nothing to release."))
    else:
        submit_service.release_log(participant=participant, actor=request.user)
        messages.success(
            request, _("Released submission for %(call)s.") % {"call": participant.callsign},
        )
    return redirect("admin_module:participant_detail", pk=participant.pk)


# --- backup / restore (M4.5) ------------------------------------------------------------------


@_staff_required
def backup_index(request):
    """Landing page with backup download + restore upload.

    The optional ``?restored=1`` query string surfaces the post-restore
    banner telling the operator to restart the container.
    """
    return render(
        request,
        "admin_module/backup.html",
        {"restored": request.GET.get("restored") == "1"},
    )


@_staff_required
@require_http_methods(["POST"])
def backup_download(request):
    """Stream the live DB as a SQLite file download. POST to avoid pre-fetchers
    and link crawlers accidentally triggering an audit row."""
    blob = backup_service.create_backup(actor=request.user)
    stamp = timezone.now().strftime("%Y-%m-%d-%H%M")
    filename = f"nmdsuite-backup-{stamp}.sqlite3"
    response = HttpResponse(blob, content_type="application/vnd.sqlite3")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(blob))
    return response


@_staff_required
@require_http_methods(["POST"])
def backup_restore(request):
    """Replace the live DB with the uploaded file. See
    :mod:`admin_module.backup_service` for details (and the multi-worker
    caveat: other gunicorn workers see stale data until container restart)."""
    uploaded = request.FILES.get("file")
    if uploaded is None:
        messages.error(request, _("Please choose a backup file to upload."))
        return redirect("admin_module:backup_index")

    file_bytes = uploaded.read()
    try:
        backup_service.restore_backup(file_bytes=file_bytes, actor=request.user)
    except backup_service.RestoreError as exc:
        messages.error(request, str(exc))
        return redirect("admin_module:backup_index")

    # After the swap, this worker's connections to the new DB are fresh,
    # but the admin's session row lived in the OLD DB. They'll be logged
    # out at the next request; surface the restart banner via querystring
    # so it survives that redirect.
    return redirect(f"{reverse('admin_module:backup_index')}?restored=1")


# --- bulk email (M4.4) ------------------------------------------------------------------------


@_staff_required
def bulk_email(request):
    """Compose-and-send a manual message to every active participant.

    Two-stage UX: GET (or POST without ``confirmed=1``) shows the form
    with a recipient-count preview; submitting again with ``confirmed=1``
    actually fires the send. Keeps the confirm-before-blast guarantee
    without needing a separate preview page.
    """
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")

    recipients = list(email_service.active_recipients(contest))
    recipient_count = len(recipients)

    if request.method == "POST":
        form = BulkEmailForm(request.POST)
        confirmed = request.POST.get("confirmed") == "1"
        if form.is_valid() and confirmed:
            result = email_service.send_bulk_email(
                contest=contest,
                subject=form.cleaned_data["subject"],
                body=form.cleaned_data["body"],
                actor=request.user,
            )
            if result.failed:
                messages.warning(
                    request,
                    _("Sent %(sent)d of %(total)d messages; %(failed)d failed.") % {
                        "sent": result.sent, "total": result.total, "failed": result.failed,
                    },
                )
            else:
                messages.success(
                    request,
                    _("Sent %(sent)d messages.") % {"sent": result.sent},
                )
            return redirect("admin_module:bulk_email")
    else:
        form = BulkEmailForm()

    return render(
        request,
        "admin_module/bulk_email.html",
        {
            "form": form,
            "contest": contest,
            "recipients": recipients,
            "recipient_count": recipient_count,
        },
    )


# --- Participant-list preview ------------------------------------------------------------------


@_staff_required
def participant_list_preview(request):
    """Render the participant list as PDF for the active contest, regardless
    of contest state. Lets staff sanity-check the layout (column widths,
    page breaks, …) BEFORE closing registration — at which point the same
    PDF gets attached to the broadcast email and is too late to fix.

    The portal-side equivalent (:func:`portal.views.participant_list`)
    keeps its registration-closed gate; participants only get to see the
    list once it's stable."""
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")

    from core.participant_list_pdf import build_participant_list_pdf

    blob = build_participant_list_pdf(contest)
    filename = f"nmd-{contest.year}-participants.pdf"
    response = HttpResponse(blob, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(blob))
    return response


@_staff_required
def participant_list_csv_preview(request):
    """Same as :func:`participant_list_preview` but for the CSV format
    consumed by dedicated logging software."""
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")

    from core.participant_list_csv import build_participant_list_csv

    blob = build_participant_list_csv(contest)
    filename = f"nmd-{contest.year}-participants.csv"
    response = HttpResponse(blob, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(blob))
    return response


# --- Ranking PDF (magazine export) -------------------------------------------------------------


@_staff_required
def ranking_pdf(request):
    """Stream the active contest's ranking + station-data table as a PDF.
    Layout mirrors the public ranking page minus the map — intended for
    the club magazine. Available regardless of contest state (admin
    judgement when to publish externally)."""
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")

    from public.ranking_pdf import build_ranking_pdf

    blob = build_ranking_pdf(contest)
    filename = f"nmd-{contest.year}-ranking.pdf"
    response = HttpResponse(blob, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(blob))
    return response


# --- Ranking preview --------------------------------------------------------------------------


@_staff_required
def ranking_preview(request):
    """Render the public ranking page against the active contest without
    the published-state gate. Lets staff see what the published ranking
    would look like while scoring/fixstation review is still in flight.
    Reuses the public template; the ``is_preview`` flag drives a banner."""
    from dataclasses import asdict

    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")

    page = build_ranking_page(contest)
    return render(
        request,
        "public/ranking.html",
        {
            "contest": contest,
            "page": page,
            "markers": [asdict(m) for m in page.markers],
            "trx_label": TRX_LABEL,
            "psu_label": PSU_LABEL,
            "antenna_label": ANTENNA_LABEL,
            "is_preview": True,
        },
    )


# --- Fixstation Review (M4B) ----------------------------------------------------------------


@_staff_required
def fixstation_review(request):
    """Surface suspicious non-NMD remote callsigns for manual verification.

    Candidates are non-NMD callsigns logged by 1 or 2 participants — the
    risk of a misheard or mistyped call is highest there. Admin ticks the
    ones that fail external-database lookup; saving rebuilds the contest's
    :class:`InvalidCallsign` set and re-runs the scoring pipeline so
    points reflect the new flags immediately.
    """
    contest = _active_contest()
    if contest is None:
        messages.error(request, _("No active contest."))
        return redirect("admin_module:index")

    if request.method == "POST":
        marked = {
            normalize_callsign(c)
            for c in request.POST.getlist("invalid")
            if c.strip()
        }
        added, removed = fixstation_service.apply_invalid_flags(
            contest=contest, marked_invalid=marked, actor=request.user,
        )
        if added or removed:
            audit(
                action="fixstation.update",
                actor=request.user,
                contest=contest,
                payload={"added": added, "removed": removed},
            )
            # Re-score so the per-QSO points reflect the new flags. The
            # centralised wrapper handles the matching scoring.run
            # audit row, tagged with source="fixstation".
            services.rescore_contest(contest, actor=request.user, source="fixstation")
            messages.success(
                request,
                _("Updated invalid-callsign list: %(added)d added, %(removed)d removed. "
                  "Scoring has been re-run.") % {"added": added, "removed": removed},
            )
        else:
            messages.info(request, _("No changes to the invalid-callsign list."))
        return redirect("admin_module:fixstation_review")

    candidates = fixstation_service.build_candidates(contest)
    return render(
        request,
        "admin_module/fixstation_review.html",
        {"contest": contest, "candidates": candidates},
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

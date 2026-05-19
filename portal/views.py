"""Participant portal views (M2.1 dashboard/edit/cancel … M2.5 submit log).

The login / logout / password-reset views live directly in ``portal.urls``
via the Django built-ins, so they aren't repeated here. All editing
endpoints route through ``_editable_participation_or_redirect`` so the
post-submit lock is enforced in one place.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from core.models import Contest, Participant, QsoEntry
from core.picker import map_picker_context
from registration.services import cancel_participation

from . import qso_service, station_service, submit_service
from .forms import QsoEntryForm, StationDataForm
from .qso_upload import UploadParseError, parse_upload


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


def _editable_participation_or_redirect(request) -> tuple[Participant | None, object]:
    """Look up the active participation AND assert it isn't already submitted.

    Returns ``(participant, None)`` on success, or ``(None, response)`` if the
    caller should return ``response`` instead. Used by every editing endpoint
    so the post-submit lock is enforced in one place.
    """
    contest = _active_contest()
    participant = _active_participation(request.user, contest)
    if participant is None:
        messages.info(request, _("You are not registered for the current contest."))
        return None, redirect("portal:dashboard")
    if participant.submitted_at is not None:
        messages.info(request, _("Your log has been submitted; further changes are not possible."))
        return None, redirect("portal:dashboard")
    return participant, None


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
            "participant_list_available": _participant_list_available(contest),
        },
    )


# --- profile edit ----------------------------------------------------------------------------


# --- cancel participation --------------------------------------------------------------------


@login_required
def cancel(request):
    participant, redirected = _editable_participation_or_redirect(request)
    if redirected is not None:
        return redirected
    contest = _active_contest()

    if request.method == "POST":
        cancel_participation(participant, actor=request.user)
        logout(request)
        messages.success(
            request,
            _("Your participation has been cancelled. You can register again any time before the contest."),
        )
        return redirect("portal:login")

    return render(request, "portal/cancel.html", {"participant": participant, "contest": contest})


# --- log entry (M2.2) ------------------------------------------------------------------------
#
# Pattern: single form at the top, table below. After every save / edit /
# delete the server returns BOTH the fresh form AND the up-to-date row list
# in one response — the form swaps into #qso-form-section (the normal target)
# and an out-of-band swap replaces the contents of #qso-list. Editing fills
# the top form (matches the legacy nmdlogsubmission UX).


def _participant_or_redirect(request):
    contest = _active_contest()
    participant = _active_participation(request.user, contest)
    if participant is None:
        messages.info(request, _("You are not registered for the current contest."))
        return None, redirect("portal:dashboard")
    return participant, None


def _own_qso(participant: Participant, pk: int) -> QsoEntry:
    qso = get_object_or_404(QsoEntry, pk=pk)
    if qso.participant_id != participant.id:
        raise Http404
    return qso


def _attach_qso_urls(qsos):
    """Attach the per-row edit/delete URLs the partial expects."""
    for q in qsos:
        q.edit_url = reverse("portal:qso_edit", args=[q.pk])
        q.delete_url = reverse("portal:qso_delete", args=[q.pk])
    return qsos


def _portal_qso_app_context() -> dict[str, str]:
    """URL endpoints the QSO partials need. Mirrors what the admin
    on-behalf surface passes, but pointing at the portal endpoints."""
    return {
        "qso_save_url": reverse("portal:qso_save"),
        "qso_list_url": reverse("portal:log_entry"),
    }


def _raw_qso_data(request) -> dict[str, str]:
    """Read the 6 QSO fields from request.POST verbatim — permissive saves
    persist exactly what the operator typed."""
    return {
        "utc": request.POST.get("utc", ""),
        "remote_call": request.POST.get("remote_call", ""),
        "rsts": request.POST.get("rsts", ""),
        "txts": request.POST.get("txts", ""),
        "rstr": request.POST.get("rstr", ""),
        "txtr": request.POST.get("txtr", ""),
    }


def _render_app(request, *, participant, form=None, editing_id=""):
    """Render the whole #qso-app fragment (form + table). Used as the
    response to every save / edit / delete so the table always reflects the
    current state. Simple full-section swap, no OOB complications."""
    return render(
        request,
        "portal/_qso_app.html",
        {
            "form": form or QsoEntryForm(),
            "editing_id": editing_id,
            "qsos": _attach_qso_urls(qso_service.list_qsos_with_warnings(participant)),
            **_portal_qso_app_context(),
        },
    )


@login_required
def log_entry(request):
    participant, redirected = _participant_or_redirect(request)
    if redirected is not None:
        return redirected
    return render(
        request,
        "portal/log_entry.html",
        {
            "participant": participant,
            "qsos": _attach_qso_urls(qso_service.list_qsos_with_warnings(participant)),
            "form": QsoEntryForm(),
            "editing_id": "",
            **_portal_qso_app_context(),
        },
    )


@login_required
@require_http_methods(["POST"])
def qso_save(request):
    participant, redirected = _editable_participation_or_redirect(request)
    if redirected is not None:
        return redirected

    data = _raw_qso_data(request)
    if not qso_service.is_all_empty(data):
        editing_id = (request.POST.get("editing_id") or "").strip()
        if editing_id:
            qso = _own_qso(participant, int(editing_id))
            qso_service.update_qso(qso=qso, data=data)
        else:
            qso_service.create_qso(participant=participant, data=data)

    return _render_app(request, participant=participant)


@login_required
def qso_edit(request, pk: int):
    """Pre-fill the top form with this row's values for inline editing."""
    participant, redirected = _editable_participation_or_redirect(request)
    if redirected is not None:
        return redirected
    qso = _own_qso(participant, pk)
    form = QsoEntryForm(initial=qso_service.initial_from_qso(qso))
    return _render_app(request, participant=participant, form=form, editing_id=qso.pk)


@login_required
@require_http_methods(["DELETE", "POST"])
def qso_delete(request, pk: int):
    participant, redirected = _editable_participation_or_redirect(request)
    if redirected is not None:
        return redirected
    qso = _own_qso(participant, pk)
    qso.delete()
    return _render_app(request, participant=participant)


# --- log upload (M2.3) -----------------------------------------------------------------------


@login_required
@require_http_methods(["POST"])
def qso_upload(request):
    """Accept a .nmd / .csv file and atomically replace the participant's QSO list.

    Posted from the portal dashboard. The wire format is the legacy 6-column
    one (``UTC;CALL;RSTS;TXTS;RSTR;TXTR``, semicolon-delimited, optional
    ``#;FIELD=;value`` station-info comment lines). An .nmd file carries
    both QSO log rows and station-description metadata; both are applied
    here, and the operator is then redirected back to the dashboard with a
    flash summary.
    """
    participant, redirected = _editable_participation_or_redirect(request)
    if redirected is not None:
        return redirected

    uploaded = request.FILES.get("file")
    if uploaded is None:
        messages.error(request, _("Please choose a file to upload."))
        return redirect("portal:dashboard")

    name = uploaded.name.lower()
    if not (name.endswith(".csv") or name.endswith(".nmd")):
        messages.error(request, _("Only .csv and .nmd files are supported."))
        return redirect("portal:dashboard")

    try:
        parsed = parse_upload(uploaded.read())
    except UploadParseError as exc:
        messages.error(request, str(exc))
        return redirect("portal:dashboard")

    count = qso_service.replace_qsos_from_upload(
        participant=participant, rows=parsed.qsos, filename=uploaded.name,
    )
    outcome = station_service.apply_upload_station_info(participant, parsed.station_info.fields)
    parts = [_("Imported %(count)d QSO entries from %(name)s.") % {"count": count, "name": uploaded.name}]
    if outcome.station_updated:
        parts.append(_("Station description updated."))
    if outcome.location_updated:
        parts.append(_("Location updated from the file; altitude and canton refreshed from Swisstopo."))
    messages.success(request, " ".join(str(p) for p in parts))
    if outcome.location_invalid:
        messages.warning(
            request,
            _("The coordinates in the file are outside Switzerland or could not be parsed — your registered location was kept."),
        )
    return redirect("portal:dashboard")


# --- station description (M2.4) --------------------------------------------------------------


@login_required
def station(request):
    """Unified station-data form.

    Covers everything the operator can edit after registration: equipment
    (operator name, output power, components) plus the editable
    registration fields (multi-op, location, coordinates, modes, remarks).
    """
    contest = _active_contest()
    participant = _active_participation(request.user, contest)
    if participant is None:
        messages.info(request, _("You are not registered for the current contest."))
        return redirect("portal:dashboard")

    if request.method == "POST":
        if participant.submitted_at is not None:
            messages.info(request, _("Your log has been submitted; further changes are not possible."))
            return redirect("portal:dashboard")
        form = StationDataForm(request.POST)
        if form.is_valid():
            payload = dict(form.cleaned_data)
            payload["operating_modes"] = form.operating_modes_value()
            station_service.save_station(participant=participant, data=payload)
            messages.success(request, _("Station data saved."))
            return redirect("portal:station")
    else:
        form = StationDataForm(
            initial=station_service.initial_from_participant(participant),
        )

    return render(
        request,
        "portal/station.html",
        {
            "form": form,
            "participant": participant,
            "contest": contest,
            "registrations_url": reverse("registration:registrations_json"),
            **map_picker_context(request),
        },
    )


# --- submit log (M2.5) -----------------------------------------------------------------------


@login_required
def submit(request):
    """Confirm-and-lock page. POST flips ``participant.submitted_at`` and
    sends the operator a trilingual confirmation email."""
    contest = _active_contest()
    participant = _active_participation(request.user, contest)
    if participant is None:
        messages.info(request, _("You are not registered for the current contest."))
        return redirect("portal:dashboard")
    if participant.submitted_at is not None:
        messages.info(request, _("Your log has already been submitted."))
        return redirect("portal:dashboard")

    if request.method == "POST":
        try:
            submit_service.submit_log(participant=participant)
        except submit_service.SubmissionRejected as exc:
            for err in exc.errors:
                messages.error(request, err)
            return redirect("portal:submit")
        messages.success(request, _("Your log has been submitted. A confirmation email is on its way."))
        return redirect("portal:dashboard")

    qsos = list(qso_service.list_qsos(participant))
    validation = submit_service.validate_for_submission(participant)
    return render(
        request,
        "portal/submit_confirm.html",
        {
            "participant": participant,
            "contest": contest,
            "qso_count": len(qsos),
            "validation": validation,
        },
    )


# --- participant list PDF (M4A.1) ------------------------------------------------------------


def _participant_list_available(contest: Contest | None) -> bool:
    """Available once registration has closed and through the rest of
    the contest lifecycle. Archived contests still show, so historical
    operators can re-download an old list if they want it."""
    if contest is None:
        return False
    return contest.state != Contest.State.REGISTRATION_OPEN


@login_required
def participant_list(request):
    """Stream the active-participants list as a PDF.

    Any authenticated operator can download (it's the same list they'd
    see on paper at the contest). Gated on the contest having moved
    past REGISTRATION_OPEN so we don't publish an incomplete roster.
    """
    contest = _active_contest()
    if not _participant_list_available(contest):
        messages.info(request, _("The participant list will be available once registration closes."))
        return redirect("portal:dashboard")

    from core.participant_list_pdf import build_participant_list_pdf

    blob = build_participant_list_pdf(contest)
    filename = f"nmd-{contest.year}-participants.pdf"
    response = HttpResponse(blob, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(blob))
    return response


# --- ADIF export -----------------------------------------------------------------------------


@login_required
def adif_download(request):
    """Stream the operator's submitted log as ADIF.

    Self-service only: each operator pulls their own log. Gated on
    ``submitted_at`` because the file is the canonical post-submit
    snapshot — exporting a half-edited draft would mislead the
    receiving logger.
    """
    contest = _active_contest()
    participant = _active_participation(request.user, contest)
    if participant is None or participant.submitted_at is None:
        messages.info(
            request,
            _("Your ADIF download will be available once you submit your log."),
        )
        return redirect("portal:dashboard")

    from core.adif_export import build_participant_adif

    text = build_participant_adif(participant)
    safe_call = participant.callsign.replace("/", "-")
    filename = f"nmd-{contest.year}-{safe_call}.adi"
    response = HttpResponse(text, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# --- scoring view (M2.6) ---------------------------------------------------------------------
#
# The operator's per-QSO scoring breakdown, gated on the contest having
# `results_published_at` set (admin flips that via M4.2's publish_results
# transition). Until then the page shows a "results not yet published"
# message. After publish, it shows the same QSO + status data the staff
# review page uses, plus a points-by-mode-and-half breakdown.


@login_required
def scoring(request):
    contest = _active_contest()
    participant = _active_participation(request.user, contest)
    if participant is None:
        messages.info(request, _("You are not registered for the current contest."))
        return redirect("portal:dashboard")

    published = contest is not None and contest.results_published_at is not None

    rows: list[dict] = []
    breakdown = None
    if published:
        from core.models import QsoEntry, ScoringRecord
        from scoring.totals import participant_breakdown

        breakdown = participant_breakdown(participant)
        qsos = (
            QsoEntry.objects
            .filter(participant=participant)
            .select_related(
                "score",
                "score__matched_qso",
                "score__matched_qso__participant",
            )
            .order_by("utc_time", "utc_raw", "id")
        )
        for q in qsos:
            try:
                score = q.score
            except ScoringRecord.DoesNotExist:
                score = None
            rows.append({"qso": q, "score": score})

    return render(
        request,
        "portal/scoring.html",
        {
            "contest": contest,
            "participant": participant,
            "published": published,
            "breakdown": breakdown,
            "rows": rows,
        },
    )

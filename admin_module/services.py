"""Contest lifecycle transitions (M4.2).

The contest progresses through states defined in
:class:`core.models.Contest.State`. Each forward transition has a
matching ``revert_*`` reverse so staff can roll back if they advance
prematurely or need to accept a late submission / fix something
before re-publishing.

State graph (forward) ::

    REGISTRATION_OPEN
          │  close_registration()           ↑ revert_close_registration()
          ▼
    REGISTRATION_CLOSED
          │  close_log_submission()         ↑ revert_close_log_submission()
          │  ← auto-submits + runs scoring  ← un-auto-submits exactly those
          ▼
    LOGS_CLOSED
          │  publish_results()              ↑ revert_publish_results()
          │  ← sets results_published_at    ← clears results_published_at
          ▼
    PUBLISHED
          │  setup_new_contest(year=YYYY)   (no reverse — too destructive)
          ▼
    ARCHIVED  + new Contest in REGISTRATION_OPEN

Note: ``LOGS_OPEN`` remains as an enum value for backward-compat with
historical data but is no longer part of the forward flow. The
operator-facing log/station editing is gated by
``participant.submitted_at``, not by the contest state — there's
nothing to "open", and the previous extra handshake was a no-op.

Notes:

- Each transition is wrapped in ``transaction.atomic``: state, side
  effects, and the audit row commit together or not at all.
- ``close_log_submission`` flips ``submitted_at`` AND ``auto_submitted``
  on every active participant who hadn't already submitted. The
  ``auto_submitted`` flag lets the matching reverse un-submit *exactly*
  those rows (and not legitimate operator submissions made earlier).
- ``setup_new_contest`` is forward-only. Reversing it would require
  remembering which contests were archived and which users were
  deactivated, and reactivating the wrong user is a security risk —
  if an admin makes this mistake they should reactivate accounts /
  unarchive contests manually via Django admin.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from core.audit import audit
from core.models import Contest, Participant

User = get_user_model()


class TransitionError(ValueError):
    """Raised when the requested transition is not allowed from the
    current state. Caller is expected to surface ``str(exc)`` as a
    flash message."""


def _require_state(contest: Contest, allowed: tuple[str, ...]) -> None:
    if contest.state not in allowed:
        raise TransitionError(
            f"Cannot transition from {contest.get_state_display()} "
            f"(state must be one of: {', '.join(allowed)})"
        )


@transaction.atomic
def close_registration(contest: Contest, *, actor) -> None:
    _require_state(contest, (Contest.State.REGISTRATION_OPEN,))
    contest.state = Contest.State.REGISTRATION_CLOSED
    contest.save(update_fields=["state"])
    audit(action="contest.close_registration", actor=actor,
          target=str(contest.year), contest=contest)


@transaction.atomic
def close_log_submission(contest: Contest, *, actor) -> int:
    """Close logs, auto-submit anyone who hadn't submitted yet, and run
    the scoring pipeline.

    Returns the number of auto-submitted participants. Sets
    ``auto_submitted=True`` alongside ``submitted_at`` so the matching
    reverse can un-submit exactly those rows without disturbing
    legitimate operator submissions.

    Scoring runs at the end so the LOGS_CLOSED state always lands with
    fresh ScoringRecord rows — no separate admin step required.
    """
    _require_state(contest, (Contest.State.REGISTRATION_CLOSED,))
    now = timezone.now()
    pending_qs = Participant.objects.filter(
        contest=contest, cancelled_at__isnull=True, submitted_at__isnull=True,
    )
    auto_submitted = pending_qs.update(submitted_at=now, auto_submitted=True)
    contest.state = Contest.State.LOGS_CLOSED
    contest.save(update_fields=["state"])
    audit(
        action="contest.close_logs", actor=actor,
        target=str(contest.year), contest=contest,
        payload={"auto_submitted": auto_submitted},
    )
    rescore_contest(contest, actor=actor, source="close_logs")
    return auto_submitted


@transaction.atomic
def rescore_contest(contest: Contest, *, actor, source: str = "manual") -> dict[str, int]:
    """Re-run the scoring pipeline for ``contest`` and audit the run.

    ``source`` tags the audit row so it's clear why the run happened:
    ``"close_logs"`` (auto, fired by the state transition),
    ``"fixstation"`` (auto, after invalid-callsign edits), or
    ``"manual"`` (admin pressed the Re-run scoring button).

    Returns the ``{status: count}`` summary from the scoring engine.
    """
    # Local import to avoid pulling scoring into admin_module at module
    # import time — the scoring engine has deeper deps and would slow
    # down management commands that don't need it.
    from scoring.pairing import score_contest

    summary = score_contest(contest)
    audit(
        action="scoring.run", actor=actor,
        target=str(contest.year), contest=contest,
        payload={"source": source, "summary": dict(summary)},
    )
    return summary


@transaction.atomic
def publish_results(contest: Contest, *, actor) -> None:
    _require_state(contest, (Contest.State.LOGS_CLOSED, Contest.State.SCORED))
    contest.state = Contest.State.PUBLISHED
    contest.results_published_at = timezone.now()
    contest.save(update_fields=["state", "results_published_at"])
    audit(action="contest.publish", actor=actor,
          target=str(contest.year), contest=contest)


# --- Reverse transitions -----------------------------------------------------------------


@transaction.atomic
def revert_close_registration(contest: Contest, *, actor) -> None:
    """REGISTRATION_CLOSED → REGISTRATION_OPEN."""
    _require_state(contest, (Contest.State.REGISTRATION_CLOSED,))
    contest.state = Contest.State.REGISTRATION_OPEN
    contest.save(update_fields=["state"])
    audit(action="contest.revert_close_registration", actor=actor,
          target=str(contest.year), contest=contest)


@transaction.atomic
def revert_close_log_submission(contest: Contest, *, actor) -> int:
    """LOGS_CLOSED → REGISTRATION_CLOSED. Un-submits ONLY the participants
    who were auto-submitted by the matching forward transition;
    legitimate operator submissions are left alone. Returns the
    un-submitted count."""
    _require_state(contest, (Contest.State.LOGS_CLOSED,))
    auto_submitted_qs = Participant.objects.filter(
        contest=contest, auto_submitted=True,
    )
    un_submitted = auto_submitted_qs.update(submitted_at=None, auto_submitted=False)
    contest.state = Contest.State.REGISTRATION_CLOSED
    contest.save(update_fields=["state"])
    audit(
        action="contest.revert_close_logs", actor=actor,
        target=str(contest.year), contest=contest,
        payload={"un_submitted": un_submitted},
    )
    return un_submitted


@transaction.atomic
def revert_publish_results(contest: Contest, *, actor) -> None:
    """PUBLISHED → LOGS_CLOSED. Clears ``results_published_at``."""
    _require_state(contest, (Contest.State.PUBLISHED,))
    contest.state = Contest.State.LOGS_CLOSED
    contest.results_published_at = None
    contest.save(update_fields=["state", "results_published_at"])
    audit(action="contest.revert_publish", actor=actor,
          target=str(contest.year), contest=contest)


@transaction.atomic
def setup_new_contest(*, year: int, actor) -> Contest:
    """Archive existing contests, deactivate non-staff accounts, and
    seed a new contest row for ``year``.

    Raises :class:`TransitionError` if a contest already exists for
    ``year`` (use ``seed_contest --year YYYY --force`` to overwrite
    that specific row instead).
    """
    if year < 2000 or year > 2100:
        raise TransitionError(f"Year {year} out of supported range")
    if Contest.objects.filter(year=year).exists():
        raise TransitionError(f"A contest for {year} already exists")

    archived = Contest.objects.exclude(state=Contest.State.ARCHIVED).update(
        state=Contest.State.ARCHIVED,
    )
    deactivated = User.objects.filter(
        is_staff=False, is_superuser=False, is_active=True,
    ).update(is_active=False)

    call_command("seed_contest", "--year", str(year))
    new_contest = Contest.objects.get(year=year)

    audit(
        action="contest.setup_new", actor=actor,
        target=str(year), contest=new_contest,
        payload={
            "archived_contests": archived,
            "deactivated_users": deactivated,
        },
    )
    return new_contest

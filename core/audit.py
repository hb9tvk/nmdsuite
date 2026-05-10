"""Tiny audit-logging helper.

Call ``audit(...)`` from any view/service that performs an admin or system
action. The call is best-effort: if the DB write fails we log the error but
never propagate the exception, since audit is observational.
"""
from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth import get_user_model

from .models import AuditLog, Contest

log = logging.getLogger("nmdsuite.audit")
User = get_user_model()


def audit(
    *,
    action: str,
    actor: Any | None = None,
    actor_label: str = "",
    target: str = "",
    contest: Contest | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditLog | None:
    """Append one row to the audit log.

    ``actor`` may be a Django User, an int user id, or None. ``actor_label`` is
    used when there is no User row (e.g. system jobs).
    """
    user = None
    if isinstance(actor, User):
        user = actor
    elif isinstance(actor, int):
        user = User.objects.filter(pk=actor).first()

    try:
        return AuditLog.objects.create(
            actor=user,
            actor_label=actor_label or (user.username if user else ""),
            action=action,
            target=target,
            contest=contest,
            payload=payload or {},
        )
    except Exception:
        log.exception("Failed to write audit log entry for action=%s target=%s", action, target)
        return None

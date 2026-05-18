"""Core domain models shared by every NMDSuite module.

The whole suite stores everything in one SQLite file. Old contests are not
deleted — they stay queryable as archived rows. The "current" contest is the
one with `state` not equal to ``ARCHIVED`` and the most recent ``year``.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


# --- Contest ---------------------------------------------------------------------------------


class Contest(models.Model):
    """One NMD edition (annual)."""

    class State(models.TextChoices):
        REGISTRATION_OPEN = "reg_open", _("Registration open")
        REGISTRATION_CLOSED = "reg_closed", _("Registration closed")
        LOGS_OPEN = "logs_open", _("Log submission open")
        LOGS_CLOSED = "logs_closed", _("Log submission closed")
        SCORED = "scored", _("Scored")
        PUBLISHED = "published", _("Results published")
        ARCHIVED = "archived", _("Archived")

    year = models.PositiveIntegerField(primary_key=True)
    contest_date = models.DateField(help_text=_("UTC date of the contest"))
    start_utc = models.DateTimeField(help_text=_("Contest start (06:00 UTC)"))
    end_utc = models.DateTimeField(help_text=_("Contest end (09:59:59 UTC)"))
    half_split_utc = models.DateTimeField(help_text=_("Boundary between H1 and H2 (08:00 UTC)"))

    state = models.CharField(max_length=16, choices=State.choices, default=State.REGISTRATION_OPEN)
    results_published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year"]

    def __str__(self) -> str:
        return f"NMD {self.year}"


# --- Participant + station data ---------------------------------------------------------------


class Participant(models.Model):
    class CoordSystem(models.TextChoices):
        WGS84 = "wgs84", "WGS84"
        CH1903 = "ch1903", "CH1903"
        CH1903PLUS = "ch1903plus", "CH1903+"

    class Mode(models.IntegerChoices):
        # Bitmask: 1=CW, 2=SSB, 3=both.
        CW = 1, "CW"
        SSB = 2, "SSB"
        BOTH = 3, _("CW + SSB")

    contest = models.ForeignKey(Contest, on_delete=models.PROTECT, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="participations")

    callsign = models.CharField(max_length=20, help_text=_("Operator/station callsign without /P"))
    first_name = models.CharField(max_length=80)
    email = models.EmailField()

    multi_op = models.BooleanField(default=False)
    station_chief = models.CharField(max_length=20, blank=True)

    coord_system_input = models.CharField(max_length=12, choices=CoordSystem.choices)
    coord_input_e = models.CharField(max_length=32, blank=True, help_text=_("Original easting/longitude as entered"))
    coord_input_n = models.CharField(max_length=32, blank=True, help_text=_("Original northing/latitude as entered"))
    location_text = models.CharField(
        max_length=120, blank=True,
        help_text=_("Friendly location name (SOTA ref, summit name, …) — the named counterpart of the coordinates"),
    )
    # Canonical CH1903+ (LV95) and WGS84 always populated for map / lookups.
    ch1903p_e = models.FloatField(null=True, blank=True)
    ch1903p_n = models.FloatField(null=True, blank=True)
    wgs84_lat = models.FloatField(null=True, blank=True)
    wgs84_lon = models.FloatField(null=True, blank=True)
    altitude_m = models.PositiveIntegerField()

    canton = models.CharField(max_length=2, help_text=_("2-letter Swiss canton code"))
    operating_modes = models.PositiveSmallIntegerField(choices=Mode.choices, default=Mode.BOTH)

    remarks = models.TextField(blank=True)

    registered_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(
        null=True, blank=True,
        help_text=_("Set when the operator finalises and locks their log + station description"),
    )
    auto_submitted = models.BooleanField(
        default=False,
        help_text=_(
            "True if submitted_at was set by admin's 'close log submission' "
            "(rather than by the operator). Used to scope the un-submit when "
            "reverting the LOGS_CLOSED → LOGS_OPEN transition."
        ),
    )

    class Meta:
        unique_together = [("contest", "user")]
        ordering = ["callsign"]

    def __str__(self) -> str:
        return f"{self.callsign} ({self.contest_id})"

    @property
    def is_active(self) -> bool:
        return self.cancelled_at is None

    @property
    def is_submitted(self) -> bool:
        return self.submitted_at is not None

    @property
    def ch1903_e(self) -> int | None:
        """Easting in CH1903 (LV03), 6-digit legacy Swiss grid. Display-only."""
        if self.ch1903p_e is None:
            return None
        return int(round(self.ch1903p_e - 2_000_000))

    @property
    def ch1903_n(self) -> int | None:
        """Northing in CH1903 (LV03), 6-digit legacy Swiss grid. Display-only."""
        if self.ch1903p_n is None:
            return None
        return int(round(self.ch1903p_n - 1_000_000))


class StationDescription(models.Model):
    """The station info that goes alongside the submitted log."""

    participant = models.OneToOneField(Participant, on_delete=models.CASCADE, related_name="station")
    op_name = models.CharField(max_length=80, blank=True)
    watt = models.CharField(max_length=20, blank=True)
    total_weight_g = models.PositiveIntegerField(default=0, help_text=_("Total station weight (grams) — used as ranking tiebreaker"))

    class Meta:
        verbose_name = _("Station description")
        verbose_name_plural = _("Station descriptions")

    def __str__(self) -> str:
        return f"Station {self.participant.callsign}"


class StationComponent(models.Model):
    """One physical part of the station (Sender, Antenne, Akku, …) with its weight."""

    station = models.ForeignKey(StationDescription, on_delete=models.CASCADE, related_name="components")
    idx = models.PositiveSmallIntegerField()
    description = models.CharField(max_length=120)
    weight_g = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["idx"]
        unique_together = [("station", "idx")]


# --- QSO log + scoring ------------------------------------------------------------------------


class QsoEntry(models.Model):
    """One row from the participant's log (one logged QSO).

    The participant portal accepts log entries permissively: invalid input is
    stored verbatim in ``utc_raw`` etc. so the operator can fix things up
    later without losing what they typed. ``utc_time`` and ``mode`` are only
    populated when the corresponding raw fields parse cleanly.

    The final "submit log" action (M2.5) is also permissive — the operator
    decides what to file. Invalid rows surface as a warning on the confirm
    page but do not block submission; the M3 scoring engine ignores rows
    where ``utc_time`` is null or ``mode`` is blank.
    """

    class Mode(models.TextChoices):
        CW = "CW", "CW"
        SSB = "SSB", "SSB"

    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="qsos")
    utc_raw = models.CharField(max_length=8, blank=True, default="")
    utc_time = models.DateTimeField(null=True, blank=True)
    mode = models.CharField(max_length=3, choices=Mode.choices, blank=True, default="")
    remote_call = models.CharField(max_length=20, blank=True, default="")
    rsts = models.CharField(max_length=3, blank=True, default="")
    txts = models.CharField(max_length=255, blank=True, default="")
    rstr = models.CharField(max_length=3, blank=True, default="")
    txtr = models.CharField(max_length=255, blank=True, default="")
    remark = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["utc_raw", "id"]
        indexes = [
            models.Index(fields=["participant", "utc_time"]),
            models.Index(fields=["remote_call", "mode", "utc_time"]),
        ]

    # --- per-field validity (computed on render, not stored) -----------------------------------

    @property
    def is_utc_valid(self) -> bool:
        from portal.qso_validators import is_valid_utc
        return is_valid_utc(self.utc_raw)

    @property
    def is_remote_call_valid(self) -> bool:
        from registration.callsigns import is_valid_callsign
        return bool(self.remote_call) and is_valid_callsign(self.remote_call)

    @property
    def is_rsts_valid(self) -> bool:
        from portal.qso_validators import is_valid_rst
        return is_valid_rst(self.rsts)

    @property
    def is_rstr_valid(self) -> bool:
        from portal.qso_validators import is_valid_rst
        return is_valid_rst(self.rstr)

    @property
    def is_txts_valid(self) -> bool:
        from portal.qso_validators import is_text_payload_valid
        return is_text_payload_valid(self.txts)

    @property
    def is_txtr_valid(self) -> bool:
        from portal.qso_validators import is_text_payload_valid
        return is_text_payload_valid(self.txtr)

    @property
    def is_rst_pair_consistent(self) -> bool:
        if not self.rsts or not self.rstr:
            return True  # incomplete row — leave the per-field flags to do the work
        return len(self.rsts) == len(self.rstr)

    @property
    def is_fully_valid(self) -> bool:
        """True only if every field is in good shape — required by the final 'submit log' action."""
        return (
            self.is_utc_valid
            and self.is_remote_call_valid
            and self.is_rsts_valid
            and self.is_rstr_valid
            and self.is_txts_valid
            and self.is_txtr_valid
            and self.is_rst_pair_consistent
        )


class ScoringStatus(models.TextChoices):
    UNMATCHED = "unmatched", _("Unmatched NMD QSO")
    FULL_MATCH = "full_match", _("Full NMD match")
    TEXT_MISMATCH = "text_mismatch", _("NMD match with text errors")
    HB9_QSO = "hb9_qso", _("Swiss non-NMD QSO")
    DX_QSO = "dx_qso", _("DX QSO")
    ADMIN_ACCEPTED = "admin_accepted", _("Admin-accepted unmatched")
    DUPE_DEDUCTED = "dupe_deducted", _("Duplicate (deducted)")
    SUSPECTED_CALL_MISMATCH = "suspected_call_mismatch", _("Possibly wrong remote callsign")


class ScoringRecord(models.Model):
    """Result of the latest scoring run for a single QSO.

    Recomputed by the scoring engine; admin overrides applied on top from
    :class:`ScoringOverride` so re-scoring is idempotent.
    """

    qso = models.OneToOneField(QsoEntry, on_delete=models.CASCADE, related_name="score")
    status = models.CharField(max_length=32, choices=ScoringStatus.choices, default=ScoringStatus.UNMATCHED)
    matched_qso = models.ForeignKey(
        QsoEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="matched_by"
    )
    points = models.PositiveSmallIntegerField(default=0)
    text_distance = models.PositiveSmallIntegerField(default=0)
    half = models.PositiveSmallIntegerField(default=1, help_text=_("1 = 06–08 UTC, 2 = 08–10 UTC"))
    suspected_correct_call = models.CharField(max_length=20, blank=True)
    admin_overridden = models.BooleanField(default=False)
    admin_comment = models.CharField(max_length=255, blank=True)
    scored_at = models.DateTimeField(auto_now=True)


class ScoringOverride(models.Model):
    """An admin scoring decision that survives across re-scoring runs.

    Keyed loosely (participant + UTC + remote_call + mode) rather than by QSO
    PK, so that re-imports of the participant's log reattach old decisions.
    """

    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="scoring_overrides")
    utc_time = models.DateTimeField()
    remote_call = models.CharField(max_length=20)
    mode = models.CharField(max_length=3)
    forced_status = models.CharField(max_length=32, choices=ScoringStatus.choices)
    comment = models.CharField(max_length=255, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="scoring_decisions"
    )
    decided_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("participant", "utc_time", "remote_call", "mode")]


# --- Cross-cutting: audit log + email log -----------------------------------------------------


class AuditLog(models.Model):
    """Append-only record of admin/system actions."""

    timestamp = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_label = models.CharField(max_length=80, blank=True, help_text=_("Free-text actor (e.g. 'system')"))
    action = models.CharField(max_length=64)
    target = models.CharField(max_length=255, blank=True)
    contest = models.ForeignKey(Contest, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit")
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["-timestamp"]),
            models.Index(fields=["action"]),
        ]


class EmailLog(models.Model):
    """Record of every outbound message — debugging mass email is a known pain point."""

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")

    sent_at = models.DateTimeField(auto_now_add=True)
    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    contest = models.ForeignKey(Contest, null=True, blank=True, on_delete=models.SET_NULL, related_name="emails")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-sent_at"]

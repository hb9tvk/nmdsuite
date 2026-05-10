"""Django admin registrations for the core models.

The Administration *module* (admin_module/) carries the workflow UIs for
contest staff. The Django admin is reserved for low-level data inspection
and developer use.
"""
from __future__ import annotations

from django.contrib import admin

from .models import (
    AuditLog,
    Contest,
    EmailLog,
    Participant,
    QsoEntry,
    ScoringOverride,
    ScoringRecord,
    StationComponent,
    StationDescription,
)


@admin.register(Contest)
class ContestAdmin(admin.ModelAdmin):
    list_display = ("year", "contest_date", "state", "results_published_at")
    list_filter = ("state",)


class StationComponentInline(admin.TabularInline):
    model = StationComponent
    extra = 0


@admin.register(StationDescription)
class StationDescriptionAdmin(admin.ModelAdmin):
    list_display = ("participant", "total_weight_g", "submitted", "submitted_at")
    list_filter = ("submitted",)
    inlines = [StationComponentInline]


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("callsign", "contest", "first_name", "canton", "operating_modes", "cancelled_at")
    list_filter = ("contest", "canton", "operating_modes", "multi_op")
    search_fields = ("callsign", "first_name", "email")


@admin.register(QsoEntry)
class QsoEntryAdmin(admin.ModelAdmin):
    list_display = ("participant", "utc_time", "mode", "remote_call", "rsts", "rstr")
    list_filter = ("mode", "participant__contest")
    search_fields = ("remote_call", "participant__callsign")


@admin.register(ScoringRecord)
class ScoringRecordAdmin(admin.ModelAdmin):
    list_display = ("qso", "status", "points", "text_distance", "admin_overridden")
    list_filter = ("status", "admin_overridden")


@admin.register(ScoringOverride)
class ScoringOverrideAdmin(admin.ModelAdmin):
    list_display = ("participant", "utc_time", "remote_call", "mode", "forced_status", "decided_by", "decided_at")
    list_filter = ("forced_status",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "actor", "actor_label", "action", "target")
    list_filter = ("action",)
    search_fields = ("action", "target", "actor_label")
    readonly_fields = ("timestamp", "actor", "actor_label", "action", "target", "contest", "payload")


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ("sent_at", "recipient", "subject", "status")
    list_filter = ("status",)
    search_fields = ("recipient", "subject")

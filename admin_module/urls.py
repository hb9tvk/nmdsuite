from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("audit/", views.audit_log, name="audit_log"),
    # Contest lifecycle transitions (M4.2). All POST-only.
    path("contest/close-registration/", views.close_registration, name="close_registration"),
    path("contest/open-logs/", views.open_log_submission, name="open_log_submission"),
    path("contest/close-logs/", views.close_log_submission, name="close_log_submission"),
    path("contest/publish/", views.publish_results, name="publish_results"),
    path("contest/revert/", views.revert_state, name="revert_state"),
    path("contest/setup-new/", views.setup_new_contest, name="setup_new_contest"),
    # On-behalf participant management (M4.3a). Participant pk in the URL
    # avoids slash-in-callsign (``HB9TVK/P``) routing problems.
    path("participants/", views.participants_index, name="participants_index"),
    path("participants/register/", views.participant_register, name="participant_register"),
    path("participants/<int:pk>/", views.participant_detail, name="participant_detail"),
    # On-behalf log + station + submit (M4.3b).
    path("participants/<int:pk>/station/", views.participant_station, name="participant_station"),
    path("participants/<int:pk>/log/", views.participant_log_entry, name="participant_log_entry"),
    path("participants/<int:pk>/log/save/", views.participant_qso_save, name="participant_qso_save"),
    path("participants/<int:pk>/log/<int:qso_pk>/edit/", views.participant_qso_edit, name="participant_qso_edit"),
    path("participants/<int:pk>/log/<int:qso_pk>/delete/", views.participant_qso_delete, name="participant_qso_delete"),
    path("participants/<int:pk>/log/upload/", views.participant_qso_upload, name="participant_qso_upload"),
    path("participants/<int:pk>/submit/", views.participant_submit, name="participant_submit"),
    path("participants/<int:pk>/release/", views.participant_release, name="participant_release"),
    # Bulk email (M4.4).
    path("email/", views.bulk_email, name="bulk_email"),
    # Backup / restore (M4.5).
    path("backup/", views.backup_index, name="backup_index"),
    path("backup/download/", views.backup_download, name="backup_download"),
    path("backup/restore/", views.backup_restore, name="backup_restore"),
]

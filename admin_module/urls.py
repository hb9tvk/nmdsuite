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
    path("participants/<int:pk>/edit-profile/", views.participant_edit_profile, name="participant_edit_profile"),
]

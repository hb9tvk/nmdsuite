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
]

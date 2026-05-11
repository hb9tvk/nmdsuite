from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views
from .forms import CallsignAuthenticationForm

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("profile/edit/", views.edit_profile, name="edit_profile"),
    path("profile/cancel/", views.cancel, name="cancel"),
    path("log/", views.log_entry, name="log_entry"),
    path("log/save/", views.qso_save, name="qso_save"),
    path("log/upload/", views.qso_upload, name="qso_upload"),
    path("log/<int:pk>/edit/", views.qso_edit, name="qso_edit"),
    path("log/<int:pk>/delete/", views.qso_delete, name="qso_delete"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="portal/login.html",
            authentication_form=CallsignAuthenticationForm,
        ),
        name="login",
    ),
    path(
        "logout/",
        auth_views.LogoutView.as_view(next_page=reverse_lazy("portal:login")),
        name="logout",
    ),
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="portal/password_reset.html",
            email_template_name="portal/email/password_reset.txt",
            subject_template_name="portal/email/password_reset_subject.txt",
            success_url=reverse_lazy("portal:password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(template_name="portal/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="portal/password_reset_confirm.html",
            success_url=reverse_lazy("portal:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="portal/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
]

"""Top-level URL routing.

Modules are exposed under distinct path prefixes so the public WordPress reverse
proxy can route /anmeldung, /submission, /scoring, /admin to the same backend.
"""
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="portal:dashboard", permanent=False)),
    path("anmeldung/", include(("registration.urls", "registration"), namespace="registration")),
    path("submission/", include(("portal.urls", "portal"), namespace="portal")),
    path("scoring/", include(("scoring.urls", "scoring"), namespace="scoring")),
    path("admin/", include(("admin_module.urls", "admin_module"), namespace="admin_module")),
    path("ranking/", include(("public.urls", "public"), namespace="public")),
    path("django-admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
]

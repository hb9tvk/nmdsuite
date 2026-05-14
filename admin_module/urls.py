from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("audit/", views.audit_log, name="audit_log"),
]

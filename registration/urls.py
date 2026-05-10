from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("success/", views.success, name="success"),
    path("registrations.json", views.registrations_json, name="registrations_json"),
]

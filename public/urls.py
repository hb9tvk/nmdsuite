"""Public URL routes — anonymous-accessible.

Year-indexed so previous-contest URLs remain stable as new editions are added.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("<int:year>/", views.ranking, name="ranking"),
]

"""Report routes."""

from django.urls import path

from . import views

urlpatterns = [
    path(
        "",
        views.stock_report,
        name="report-stock",
    ),
    path(
        "movements/",
        views.movement_report,
        name="report-movements",
    ),
]
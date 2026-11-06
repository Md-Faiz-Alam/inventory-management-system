"""Supplier directory routes."""

from django.urls import path

from . import views


urlpatterns = [
    path("", views.supplier_list, name="supplier-list"),
    path("new/", views.supplier_edit, name="supplier-create"),
    path(
        "<int:pk>/",
        views.supplier_detail,
        name="supplier-detail",
    ),
    path(
        "<int:pk>/edit/",
        views.supplier_edit,
        name="supplier-update",
    ),
    path(
        "<int:pk>/status/",
        views.supplier_status,
        name="supplier-status",
    ),
]
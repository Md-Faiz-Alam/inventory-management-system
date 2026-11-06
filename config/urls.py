"""Application URL configuration."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from inventory import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(
            template_name="accounts/login.html",
            next_page=reverse_lazy("overview"),
        ),
        name="login",
    ),
    path(
        "accounts/logout/",
        auth_views.LogoutView.as_view(
            next_page=reverse_lazy("login"),
        ),
        name="logout",
    ),
    path("", views.overview, name="overview"),
    path("products/", views.product_list, name="product-list"),
    path("products/new/", views.product_create, name="product-create"),
    path(
        "products/<int:pk>/edit/",
        views.product_update,
        name="product-update",
    ),
    path(
        "products/<int:pk>/status/",
        views.product_status,
        name="product-status",
    ),
    path("stock/", views.stock_list, name="stock-list"),
    path(
        "products/<int:pk>/receive/",
        views.stock_create,
        {"kind": "receive"},
        name="stock-receive",
    ),
    path(
        "products/<int:pk>/issue/",
        views.stock_create,
        {"kind": "issue"},
        name="stock-issue",
    ),
    path("movements/", views.movement_list, name="movement-list"),
]
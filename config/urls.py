"""Application URL configuration."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from inventory.views import overview

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
    path("", overview, name="overview"),
]
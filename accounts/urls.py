from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

urlpatterns = [
    path("login/", views.RateLimitedLoginView.as_view(), name="login"),
    path(
        "logout/",
        auth_views.LogoutView.as_view(next_page=reverse_lazy("login")),
        name="logout",
    ),
    path("guest/", views.guest_login, name="guest-login"),
    path("profile/", views.profile, name="profile"),
    path("users/", views.user_list, name="user-list"),
    path("users/new/", views.user_create, name="user-create"),
    path("users/<int:pk>/edit/", views.user_edit, name="user-edit"),
    path("users/<int:pk>/password/", views.user_password, name="user-password"),
]

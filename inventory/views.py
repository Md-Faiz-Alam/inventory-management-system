"""Views for the inventory workspace."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.cache import never_cache


@never_cache
@login_required(login_url="login")
def overview(request):
    """Display the authenticated user's workspace."""
    user = request.user

    if user.is_superuser:
        access_label = "Administrator"
    elif user.is_staff:
        access_label = "Administration access"
    else:
        access_label = "Member"

    return render(
        request,
        "inventory/overview.html",
        {
            "display_name": user.first_name or user.get_username(),
            "access_label": access_label,
        },
    )
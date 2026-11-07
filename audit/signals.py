"""Record sign-in activity."""

from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from .log import record
from .models import AuditEntry


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    record(request, AuditEntry.Action.LOGIN, "Signed in", user=user)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        record(request, AuditEntry.Action.LOGOUT, "Signed out", user=user)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    username = str(credentials.get("username", ""))[:150]
    record(
        request,
        AuditEntry.Action.LOGIN_FAILED,
        f"Failed sign-in for '{username}'",
        username=username,
    )

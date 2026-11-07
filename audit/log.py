"""Helpers for writing audit entries from views and services."""

from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.models import ContentType

from .models import AuditEntry


def client_ip(request):
    """The visitor's IP address.

    X-Forwarded-For is never trusted: clients can write anything into it.
    Behind a proxy that overwrites a header with the real address (Render
    sits behind Cloudflare, which sets True-Client-IP), name that header in
    settings.CLIENT_IP_HEADER, e.g. "HTTP_TRUE_CLIENT_IP".
    """
    if request is None:
        return None
    header = getattr(settings, "CLIENT_IP_HEADER", "")
    if header:
        value = request.META.get(header, "").strip()
        if value:
            return value[:45]
    return request.META.get("REMOTE_ADDR") or None


def plain(value):
    """Convert model values into JSON-friendly values."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if hasattr(value, "pk"):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [plain(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def snapshot(instance, fields):
    return {field: plain(getattr(instance, field)) for field in fields}


def diff(before, after):
    """Return {field: [old, new]} for changed fields only.

    A missing value and an empty one count as the same, so creating a record
    does not list every blank field as a change.
    """

    def norm(value):
        return "" if value is None else value

    return {
        key: [before.get(key), after.get(key)]
        for key in after
        if norm(before.get(key)) != norm(after.get(key))
    }


def record(request, action, message, obj=None, changes=None, user=None, username=""):
    """Write one audit entry for the current (or given) user."""
    actor = user if user is not None else getattr(request, "user", None)
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None

    entry = AuditEntry(
        actor=actor,
        actor_username=actor.get_username() if actor else username[:150],
        action=action,
        message=message[:300],
        changes=changes or {},
        ip_address=client_ip(request),
    )
    if obj is not None:
        entry.content_type = ContentType.objects.get_for_model(obj)
        entry.object_id = str(obj.pk)
        entry.object_repr = str(obj)[:200]
    entry.save()
    return entry


def history_for(obj, limit=10):
    return AuditEntry.objects.filter(
        content_type=ContentType.objects.get_for_model(obj),
        object_id=str(obj.pk),
    ).select_related("actor")[:limit]

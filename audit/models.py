"""Append-only record of who changed what, and when."""

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models


class AuditEntry(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Created"
        UPDATE = "update", "Updated"
        STATUS = "status", "Status changed"
        STOCK = "stock", "Stock changed"
        ORDER = "order", "Order workflow"
        LOGIN = "login", "Signed in"
        LOGOUT = "logout", "Signed out"
        LOGIN_FAILED = "login_failed", "Sign-in failed"
        SECURITY = "security", "Account security"

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    actor_username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=20, choices=Action.choices)
    content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.SET_NULL
    )
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    message = models.CharField(max_length=300)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name_plural = "audit entries"
        indexes = [
            models.Index(
                fields=["content_type", "object_id", "-created_at"],
                name="audit_object_history_idx",
            )
        ]

    def __str__(self):
        return f"{self.actor_username or 'system'}: {self.message}"

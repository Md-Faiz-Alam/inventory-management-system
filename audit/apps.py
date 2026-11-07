from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "audit"
    verbose_name = "Activity log"

    def ready(self):
        from . import signals  # noqa: F401

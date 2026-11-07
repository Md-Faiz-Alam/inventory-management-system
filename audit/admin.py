from django.contrib import admin

from .models import AuditEntry


@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin):
    list_display = ["created_at", "actor_username", "action", "message", "ip_address"]
    list_filter = ["action"]
    search_fields = ["actor_username", "message", "object_repr"]
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

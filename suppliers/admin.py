"""Read-only administration for supplier records."""

from django.contrib import admin

from .models import Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "code",
        "contact_name",
        "email",
        "phone",
        "is_active",
    ]
    list_filter = ["is_active"]
    search_fields = [
        "name",
        "code",
        "contact_name",
        "email",
        "phone",
    ]
    readonly_fields = [
        "name",
        "code",
        "contact_name",
        "email",
        "phone",
        "address",
        "notes",
        "products",
        "is_active",
        "created_at",
        "updated_at",
    ]
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
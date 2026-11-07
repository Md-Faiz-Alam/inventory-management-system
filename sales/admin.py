from django.contrib import admin

from .models import Customer, SalesOrder, SalesOrderLine


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "email", "phone", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "code", "email"]
    actions = None

    def has_delete_permission(self, request, obj=None):
        return False


class SalesOrderLineInline(admin.TabularInline):
    model = SalesOrderLine
    extra = 0
    readonly_fields = ["product", "quantity", "unit_price", "movement"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    list_display = ["number", "customer", "status", "created_at", "fulfilled_at"]
    list_filter = ["status"]
    search_fields = ["number", "customer__name"]
    readonly_fields = [
        "number",
        "customer",
        "status",
        "notes",
        "actor",
        "fulfilled_by",
        "fulfilled_at",
        "created_at",
        "updated_at",
    ]
    inlines = [SalesOrderLineInline]
    actions = None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

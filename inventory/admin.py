"""Administration for the inventory catalog."""

from django.contrib import admin

from .forms import ProductForm
from .models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductForm
    list_display = [
        "name",
        "sku",
        "category",
        "unit_price",
        "is_active",
        "updated_at",
    ]
    list_filter = ["is_active", "category"]
    search_fields = ["name", "sku", "category"]
    readonly_fields = ["is_active", "created_at", "updated_at"]
    actions = None

    def has_delete_permission(self, request, obj=None):
        """Preserve catalog records for future stock history."""
        return False
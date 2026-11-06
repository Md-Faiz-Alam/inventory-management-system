"""Administration for products and stock records."""

from django.contrib import admin

from .forms import ProductForm
from .models import Product, StockBalance, StockMovement


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

    def save_model(self, request, obj, form, change):
        if change:
            obj.save(
                update_fields=[*ProductForm.Meta.fields, "updated_at"]
            )
        else:
            obj.save()

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyStockAdmin(admin.ModelAdmin):
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockBalance)
class StockBalanceAdmin(ReadOnlyStockAdmin):
    list_display = ["product", "quantity", "updated_at"]
    search_fields = ["product__name", "product__sku"]
    list_select_related = ["product"]
    readonly_fields = ["product", "quantity", "updated_at"]


@admin.register(StockMovement)
class StockMovementAdmin(ReadOnlyStockAdmin):
    list_display = [
        "created_at",
        "product",
        "kind",
        "quantity",
        "balance_before",
        "balance_after",
        "actor",
    ]
    list_filter = ["kind"]
    search_fields = [
        "product__name",
        "product__sku",
        "reason",
        "actor__username",
    ]
    list_select_related = ["product", "actor"]
    readonly_fields = [
        "product",
        "actor",
        "operation_id",
        "kind",
        "quantity",
        "reason",
        "balance_before",
        "balance_after",
        "created_at",
    ]
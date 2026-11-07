from rest_framework import serializers

from inventory.models import MAX_STOCK_QUANTITY, Product, PurchaseOrder, StockMovement
from sales.models import Customer, SalesOrder, SalesOrderLine
from suppliers.models import Supplier


class ProductSerializer(serializers.ModelSerializer):
    on_hand = serializers.IntegerField(read_only=True)
    stock_status = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "sku",
            "category",
            "description",
            "unit_price",
            "reorder_level",
            "is_active",
            "on_hand",
            "stock_status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["is_active", "created_at", "updated_at"]

    def get_stock_status(self, obj) -> str:
        on_hand = getattr(obj, "on_hand", None)
        if on_hand is None:
            return ""
        if on_hand == 0:
            return "out"
        return "low" if on_hand <= obj.reorder_level else "healthy"

    def validate_sku(self, value):
        value = value.strip().upper()
        duplicates = Product.objects.filter(sku__iexact=value)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError("This SKU is already in use.")
        return value


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = [
            "id",
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
        read_only_fields = ["is_active", "created_at", "updated_at"]

    def validate_code(self, value):
        return unique_code(Supplier, value, self.instance)


def unique_code(model, value, instance):
    """Codes are unique ignoring case; check before the database refuses."""
    value = value.strip().upper()
    duplicates = model.objects.filter(code__iexact=value)
    if instance is not None:
        duplicates = duplicates.exclude(pk=instance.pk)
    if duplicates.exists():
        raise serializers.ValidationError("This code is already in use.")
    return value


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = [
            "id",
            "name",
            "code",
            "email",
            "phone",
            "gstin",
            "address",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["is_active", "created_at", "updated_at"]

    def validate_code(self, value):
        return unique_code(Customer, value, self.instance)


class StockMovementSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    actor = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "product",
            "product_sku",
            "kind",
            "quantity",
            "reason",
            "balance_before",
            "balance_after",
            "purchase_order",
            "reversal_of",
            "document_reference",
            "counted_quantity",
            "actor",
            "operation_id",
            "created_at",
        ]
        read_only_fields = fields


class StockMovementCreateSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    kind = serializers.ChoiceField(choices=StockMovement.Kind.choices)
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_STOCK_QUANTITY)
    reason = serializers.CharField(max_length=500)
    operation_id = serializers.UUIDField(
        help_text="Client-generated UUID. Re-sending the same request is safe."
    )


class PurchaseOrderSerializer(serializers.ModelSerializer):
    status = serializers.CharField(read_only=True)
    outstanding = serializers.IntegerField(read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "reference",
            "supplier",
            "product",
            "quantity",
            "received_quantity",
            "outstanding",
            "unit_cost",
            "status",
            "cancelled",
            "created_at",
        ]
        read_only_fields = fields


class SalesOrderLineSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    line_total = serializers.DecimalField(max_digits=24, decimal_places=2, read_only=True)

    class Meta:
        model = SalesOrderLine
        fields = ["product", "product_sku", "quantity", "unit_price", "line_total"]


class SalesOrderSerializer(serializers.ModelSerializer):
    lines = SalesOrderLineSerializer(many=True, read_only=True)
    total = serializers.DecimalField(max_digits=24, decimal_places=2, read_only=True)

    class Meta:
        model = SalesOrder
        fields = [
            "id",
            "number",
            "customer",
            "status",
            "notes",
            "lines",
            "total",
            "created_at",
            "fulfilled_at",
        ]
        read_only_fields = fields

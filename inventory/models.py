"""Product catalog, stock balances, and stock movement records."""

from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from django.db import models
from django.db.models.functions import Lower

MAX_STOCK_QUANTITY = 9_223_372_036_854_775_807


class Product(models.Model):
    """A catalog item whose stock is managed through stock movements."""

    name = models.CharField(max_length=160)
    sku = models.CharField(
        "SKU",
        max_length=40,
        unique=True,
        validators=[
            RegexValidator(
                regex=r"^[A-Za-z0-9_-]+$",
                message="Use letters, numbers, hyphens, and underscores only.",
            )
        ],
    )
    category = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True, max_length=3000)
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    reorder_level = models.PositiveIntegerField(default=5)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(
                Lower("sku"),
                name="product_sku_case_insensitive_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0),
                name="product_unit_price_nonnegative",
            ),
        ]

    def save(self, *args, **kwargs):
        self.sku = self.sku.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.sku})"


class StockBalance(models.Model):
    """Current stock, maintained by the stock transaction service."""

    if TYPE_CHECKING:
        product_id: int

    product = models.OneToOneField(
        Product,
        on_delete=models.PROTECT,
        related_name="stock_balance",
    )
    quantity = models.PositiveBigIntegerField(
        default=0,
        validators=[
            MinValueValidator(0),
            MaxValueValidator(MAX_STOCK_QUANTITY),
        ],
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Product {self.product_id}: {self.quantity}"


class StockMovement(models.Model):
    """A recorded receipt or issue and its resulting stock balance."""

    if TYPE_CHECKING:
        product_id: int
        actor_id: int
        purchase_order_id: int | None
        reversal_of_id: int | None

        def get_kind_display(self) -> str: ...

    class Kind(models.TextChoices):
        RECEIVE = "receive", "Stock received"
        ISSUE = "issue", "Stock issued"

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="stock_movements",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="stock_movements",
    )
    purchase_order = models.ForeignKey(
        "PurchaseOrder",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    reversal_of = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal"
    )
    document_reference = models.CharField(max_length=100, blank=True)
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    counted_quantity = models.PositiveBigIntegerField(null=True, blank=True)
    operation_id = models.UUIDField(unique=True, editable=False)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    quantity = models.PositiveBigIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(MAX_STOCK_QUANTITY),
        ],
    )
    reason = models.CharField(max_length=500)
    balance_before = models.PositiveBigIntegerField()
    balance_after = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        permissions = [
            ("receive_stock", "Can receive stock"),
            ("issue_stock", "Can issue stock"),
            ("correct_stock", "Can reverse movements and record stocktakes"),
        ]
        indexes = [
            models.Index(
                fields=["product", "-created_at", "-id"],
                name="stock_product_history_idx",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(unit_cost__isnull=True) | models.Q(unit_cost__gte=0),
                name="movement_cost_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(counted_quantity__isnull=True)
                | models.Q(
                    balance_after=models.F("counted_quantity"),
                    purchase_order__isnull=True,
                    reversal_of__isnull=True,
                ),
                name="movement_count_matches_balance",
            ),
            models.CheckConstraint(
                condition=models.Q(reversal_of__isnull=True)
                | ~models.Q(pk=models.F("reversal_of")),
                name="movement_not_own_reversal",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="stock_movement_quantity_positive",
            ),
            models.CheckConstraint(
                condition=~models.Q(reason=""),
                name="stock_movement_reason_not_empty",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind="receive",
                        quantity=(
                            models.F("balance_after") - models.F("balance_before")
                        ),
                    )
                    | models.Q(
                        kind="issue",
                        quantity=(
                            models.F("balance_before") - models.F("balance_after")
                        ),
                    )
                ),
                name="stock_movement_balances_consistent",
            ),
        ]

    def __str__(self):
        return (
            f"{self.get_kind_display()}: {self.quantity} for product {self.product_id}"
        )


class PurchaseOrder(models.Model):
    """An immutable, single-product order; receipts may arrive in parts."""

    if TYPE_CHECKING:
        supplier_id: int
        product_id: int
        actor_id: int

    reference = models.CharField(max_length=80, unique=True)
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveBigIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(MAX_STOCK_QUANTITY)]
    )
    received_quantity = models.PositiveBigIntegerField(default=0, editable=False)
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    cancelled = models.BooleanField(default=False, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="po_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(received_quantity__lte=models.F("quantity")),
                name="po_received_within_order",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_cost__gte=0), name="po_cost_nonnegative"
            ),
            models.UniqueConstraint(Lower("reference"), name="po_reference_ci_unique"),
        ]

    @property
    def outstanding(self):
        return self.quantity - self.received_quantity

    @property
    def status(self):
        if self.cancelled:
            return "Cancelled"
        if self.received_quantity == self.quantity:
            return "Complete"
        return "Partially received" if self.received_quantity else "Open"

    def __str__(self):
        return self.reference
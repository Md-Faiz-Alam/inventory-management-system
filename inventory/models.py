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
        ]
        indexes = [
            models.Index(
                fields=["product", "-created_at", "-id"],
                name="stock_product_history_idx",
            )
        ]
        constraints = [
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
                            models.F("balance_after")
                            - models.F("balance_before")
                        ),
                    )
                    | models.Q(
                        kind="issue",
                        quantity=(
                            models.F("balance_before")
                            - models.F("balance_after")
                        ),
                    )
                ),
                name="stock_movement_balances_consistent",
            ),
        ]

    def __str__(self):
        return (
            f"{self.get_kind_display()}: "
            f"{self.quantity} for product {self.product_id}"
        )
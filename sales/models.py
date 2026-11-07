"""Customers and sales orders that issue stock when fulfilled."""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower

from inventory.models import MAX_STOCK_QUANTITY, Product, StockMovement


class Customer(models.Model):
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=40, unique=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True, max_length=1000)
    gstin = models.CharField("GSTIN", max_length=15, blank=True)
    notes = models.TextField(blank=True, max_length=2000)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(Lower("code"), name="customer_code_ci_unique"),
        ]

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code})"


class SalesOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        FULFILLED = "fulfilled", "Fulfilled"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=30, unique=True, editable=False)
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="orders"
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT, editable=False
    )
    notes = models.CharField(max_length=500, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sales_orders",
    )
    fulfilled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fulfilled_sales_orders",
        editable=False,
    )
    fulfilled_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        permissions = [("fulfil_salesorder", "Can fulfil sales orders")]

    @property
    def total(self):
        return sum((line.line_total for line in self.lines.all()), Decimal("0.00"))

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT

    def __str__(self):
        return self.number


class SalesOrderLine(models.Model):
    order = models.ForeignKey(
        SalesOrder, on_delete=models.CASCADE, related_name="lines"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="sales_lines"
    )
    quantity = models.PositiveBigIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(MAX_STOCK_QUANTITY)]
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    movement = models.OneToOneField(
        StockMovement,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="sales_line",
        editable=False,
    )

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "product"], name="sales_line_product_once"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="sales_line_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0), name="sales_line_price_nonnegative"
            ),
        ]

    @property
    def line_total(self):
        return (self.unit_price or Decimal("0")) * (self.quantity or 0)

    def __str__(self):
        return f"{self.order_id}: {self.quantity} × {self.product_id}"

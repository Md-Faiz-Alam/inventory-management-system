"""Inventory catalog models."""

from decimal import Decimal

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models.functions import Lower


class Product(models.Model):
    """A catalog item whose stock will be tracked through transactions."""

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
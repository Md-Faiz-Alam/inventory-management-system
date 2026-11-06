"""Supplier contacts and product associations."""

from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower


class Supplier(models.Model):
    """A supplier that can be associated with multiple catalog products."""

    name = models.CharField(max_length=160)
    code = models.CharField(
        max_length=40,
        unique=True,
        validators=[
            RegexValidator(
                regex=r"^[A-Za-z0-9_-]+$",
                message="Use letters, numbers, hyphens, and underscores only.",
            )
        ],
    )
    contact_name = models.CharField(max_length=160, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(max_length=1000, blank=True)
    notes = models.TextField(max_length=3000, blank=True)
    products = models.ManyToManyField(
        "inventory.Product",
        related_name="suppliers",
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(
                Lower("code"),
                name="supplier_code_ci_unique",
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""),
                name="supplier_name_not_empty",
            ),
            models.CheckConstraint(
                condition=~models.Q(code=""),
                name="supplier_code_not_empty",
            ),
        ]

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code})"
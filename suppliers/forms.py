"""Validated supplier forms."""

from typing import cast

from django import forms
from config.editing import ConcurrentEditForm
from django.db.models import Q

from inventory.models import Product

from .models import Supplier


SUPPLIER_DETAIL_FIELDS = (
    "name",
    "code",
    "contact_name",
    "email",
    "phone",
    "address",
    "notes",
)


class SupplierForm(ConcurrentEditForm):
    class Meta:
        model = Supplier
        fields = [*SUPPLIER_DETAIL_FIELDS, "products"]
        help_texts = {
            "code": "A unique identifier, for example SUP-001.",
            "phone": "Include the country code where applicable.",
            "products": (
                "Select the products supplied by this supplier. "
                "Hold Ctrl on Windows to select several. "
                "Clear all selections to remove all product links."
            ),
        }
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 4}),
            "products": forms.SelectMultiple(attrs={"size": 8}),
        }

    def __init__(self, *args, allow_products=False, **kwargs):
        super().__init__(*args, **kwargs)

        if not allow_products:
            self.fields.pop("products")
            return

        products = Product.objects.filter(is_active=True)

        if self.instance.pk:
            products = Product.objects.filter(
                Q(is_active=True) | Q(suppliers__pk=self.instance.pk)
            ).distinct()

        product_field = cast(
            forms.ModelMultipleChoiceField,
            self.fields["products"],
        )
        product_field.queryset = products

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        duplicates = Supplier.objects.filter(code__iexact=code)

        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)

        if duplicates.exists():
            raise forms.ValidationError(
                "This code is already assigned to a supplier, "
                "including archived suppliers."
            )

        return code
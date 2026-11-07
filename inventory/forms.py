"""Validated product and stock forms."""

from typing import cast
from uuid import uuid4

from django import forms

from config.editing import ConcurrentEditForm

from .models import MAX_STOCK_QUANTITY, Product, PurchaseOrder, StockMovement


class ProductForm(ConcurrentEditForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "sku",
            "category",
            "description",
            "unit_price",
            "reorder_level",
        ]
        labels = {
            "unit_price": "Unit price (INR)",
        }
        help_texts = {
            "sku": "A unique identifier, for example USB-C-001.",
            "reorder_level": "Stock at or below this quantity needs attention.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "unit_price": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "reorder_level": forms.NumberInput(attrs={"min": "0", "step": "1"}),
        }

    def clean_sku(self):
        sku = self.cleaned_data["sku"].strip().upper()
        duplicates = Product.objects.filter(sku__iexact=sku)

        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)

        if duplicates.exists():
            raise forms.ValidationError(
                "This SKU is already assigned to a product, including archived products."
            )

        return sku


class StockMovementForm(forms.Form):
    operation_id = forms.UUIDField(
        initial=uuid4,
        widget=forms.HiddenInput(),
    )
    quantity = forms.IntegerField(
        min_value=1,
        max_value=MAX_STOCK_QUANTITY,
        help_text="Enter the number of units to receive or issue.",
        widget=forms.NumberInput(
            attrs={"min": "1", "step": "1", "inputmode": "numeric"}
        ),
    )
    reason = forms.CharField(
        max_length=500,
        strip=True,
        help_text="For example: supplier delivery or customer order reference.",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Why is this stock changing?",
            }
        ),
    )


class MovementFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        max_length=100,
        label="Search",
        widget=forms.SearchInput(
            attrs={"placeholder": "Product, SKU, reason, or username"}
        ),
    )
    kind = forms.ChoiceField(
        required=False,
        label="Movement",
        choices=[("", "All movements"), *StockMovement.Kind.choices],
    )


class PurchaseOrderForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ["reference", "supplier", "product", "quantity", "unit_cost"]
        labels = {"unit_cost": "Agreed unit cost (INR)"}
        widgets = {
            "quantity": forms.NumberInput(attrs={"min": "1", "step": "1"}),
            "unit_cost": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        from suppliers.models import Supplier

        super().__init__(*args, **kwargs)
        supplier_field = cast(forms.ModelChoiceField, self.fields["supplier"])
        supplier_field.queryset = Supplier.objects.filter(is_active=True)
        product_field = cast(forms.ModelChoiceField, self.fields["product"])
        product_field.queryset = Product.objects.filter(is_active=True)

    def clean_reference(self):
        reference = self.cleaned_data["reference"].strip().upper()
        if PurchaseOrder.objects.filter(reference__iexact=reference).exists():
            raise forms.ValidationError("This order reference already exists.")
        return reference


class PurchaseReceiptForm(StockMovementForm):
    document_reference = forms.CharField(
        max_length=100,
        label="Delivery document reference",
        help_text="Supplier invoice or delivery challan number.",
    )

    field_order = ["quantity", "document_reference", "reason"]


class CorrectionForm(forms.Form):
    operation_id = forms.UUIDField(initial=uuid4, widget=forms.HiddenInput())
    reason = forms.CharField(
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Why is this correction needed?"}),
    )


class StocktakeForm(CorrectionForm):
    counted_quantity = forms.IntegerField(
        min_value=0,
        max_value=MAX_STOCK_QUANTITY,
        label="Counted quantity",
        help_text="The number of units physically counted on the shelf.",
        widget=forms.NumberInput(attrs={"min": "0", "step": "1", "inputmode": "numeric"}),
    )

    field_order = ["counted_quantity", "reason"]
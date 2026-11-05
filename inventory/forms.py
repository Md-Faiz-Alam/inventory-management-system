"""Validated product forms."""

from django import forms

from .models import Product


class ProductForm(forms.ModelForm):
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
            "reorder_level": "The threshold used for future low-stock alerts.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "unit_price": forms.NumberInput(
                attrs={"min": "0", "step": "0.01"}
            ),
            "reorder_level": forms.NumberInput(
                attrs={"min": "0", "step": "1"}
            ),
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
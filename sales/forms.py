"""Customer and sales order forms."""

from typing import cast

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from config.editing import ConcurrentEditForm
from inventory.models import Product

from .models import Customer, SalesOrder, SalesOrderLine

CUSTOMER_FIELDS = ["name", "code", "email", "phone", "gstin", "address", "notes"]


class CustomerForm(ConcurrentEditForm):
    class Meta:
        model = Customer
        fields = CUSTOMER_FIELDS
        help_texts = {"code": "A unique identifier, for example CUST-001."}
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        duplicates = Customer.objects.filter(code__iexact=code)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("This customer code is already in use.")
        return code


class SalesOrderForm(forms.ModelForm):
    class Meta:
        model = SalesOrder
        fields = ["customer", "notes"]
        widgets = {"notes": forms.TextInput(attrs={"placeholder": "Optional note"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        customers = Customer.objects.filter(is_active=True)
        if self.instance.pk:
            customers = Customer.objects.filter(is_active=True) | Customer.objects.filter(
                pk=self.instance.customer_id
            )
        customer_field = cast(forms.ModelChoiceField, self.fields["customer"])
        customer_field.queryset = customers.distinct()


class SalesOrderLineForm(forms.ModelForm):
    class Meta:
        model = SalesOrderLine
        fields = ["product", "quantity", "unit_price"]
        widgets = {
            "quantity": forms.NumberInput(attrs={"min": "1", "step": "1"}),
            "unit_price": forms.NumberInput(
                attrs={"min": "0", "step": "0.01", "placeholder": "Catalog price"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        product_field = cast(forms.ModelChoiceField, self.fields["product"])
        product_field.queryset = Product.objects.filter(is_active=True)
        self.fields["unit_price"].required = False

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        if product and cleaned.get("unit_price") is None:
            cleaned["unit_price"] = product.unit_price
            self.instance.unit_price = product.unit_price
        return cleaned


class BaseLineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        products = set()
        active = 0
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            product = form.cleaned_data.get("product")
            if product is None:
                continue
            if product.pk in products:
                raise forms.ValidationError("Each product can appear only once per order.")
            products.add(product.pk)
            active += 1
        if active == 0:
            raise forms.ValidationError("Add at least one product line.")


SalesOrderLineFormSet = inlineformset_factory(
    SalesOrder,
    SalesOrderLine,
    form=SalesOrderLineForm,
    formset=BaseLineFormSet,
    extra=1,
    can_delete=True,
    max_num=50,
    validate_max=True,
)


class SalesFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        max_length=100,
        label="Search",
        widget=forms.SearchInput(attrs={"placeholder": "Order number or customer"}),
    )
    status = forms.ChoiceField(
        required=False,
        choices=[("", "All statuses"), *SalesOrder.Status.choices],
    )

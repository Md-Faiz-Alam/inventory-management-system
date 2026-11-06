"""Validated report filters shared by HTML and CSV output."""

from django import forms

from inventory.models import StockMovement


class StockReportForm(forms.Form):
    q = forms.CharField(
        required=False,
        max_length=100,
        label="Search",
        widget=forms.SearchInput(
            attrs={"placeholder": "Product, SKU, or category"}
        ),
    )
    health = forms.ChoiceField(
        required=False,
        label="Stock level",
        choices=[
            ("", "All stock levels"),
            ("healthy", "Healthy"),
            ("low", "Low stock"),
            ("out", "Out of stock"),
        ],
    )


class MovementReportForm(forms.Form):
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
    start = forms.DateField(
        required=False,
        label="From date",
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(
            attrs={"type": "date"},
            format="%Y-%m-%d",
        ),
    )
    end = forms.DateField(
        required=False,
        label="Through date",
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(
            attrs={"type": "date"},
            format="%Y-%m-%d",
        ),
    )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start")
        end = cleaned.get("end")

        if start and end and start > end:
            self.add_error(
                "end",
                "Through date must be on or after From date.",
            )

        return cleaned
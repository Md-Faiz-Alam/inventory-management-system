"""Signed edit tokens checked under the same lock as the update."""

from django import forms
from django.core import signing


def edit_token(instance):
    return signing.dumps(
        [instance._meta.label_lower, instance.pk, instance.updated_at.isoformat()],
        salt="catalog-edit",
    )


class ConcurrentEditForm(forms.ModelForm):
    edit_token = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["edit_token"].required = True
            self.initial["edit_token"] = edit_token(self.instance)

    def matches_current(self, current):
        try:
            submitted = signing.loads(
                self.cleaned_data.get("edit_token", ""), salt="catalog-edit"
            )
        except signing.BadSignature:
            submitted = None
        expected = [
            current._meta.label_lower,
            current.pk,
            current.updated_at.isoformat(),
        ]
        if submitted != expected:
            self.add_error(
                None,
                "This record changed after you opened it. Reload the page and apply your changes again.",
            )
            return False
        return True
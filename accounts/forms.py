"""User management forms."""

from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User
from .roles import ROLE_DESCRIPTIONS, ROLE_ORDER

ROLE_CHOICES = [(role, f"{role} — {ROLE_DESCRIPTIONS[role]}") for role in ROLE_ORDER]


class UserCreateForm(UserCreationForm):
    role = forms.ChoiceField(choices=ROLE_CHOICES, initial="Staff")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ["username", "first_name", "last_name", "email"]


class UserEditForm(forms.ModelForm):
    role = forms.ChoiceField(choices=ROLE_CHOICES)

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "is_active"]
        labels = {"is_active": "Account active"}
        help_texts = {"is_active": "Inactive users cannot sign in. History is kept."}


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]

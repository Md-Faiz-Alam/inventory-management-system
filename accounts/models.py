"""User model for the inventory application."""

from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Application user with Django's authentication and permissions."""

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self) -> str:
        return self.get_username()
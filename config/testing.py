"""Shared helpers for tests."""

from uuid import uuid4

from django.contrib.auth import get_user_model

from accounts.roles import set_role, sync_roles
from inventory.models import Product
from inventory.services import record_stock_movement
from suppliers.models import Supplier

PASSWORD = "Workspace-test-password-482!"


def make_user(username, role=None, **extra):
    sync_roles()
    user = get_user_model().objects.create_user(
        username=username, password=PASSWORD, email=f"{username}@example.com", **extra
    )
    if role:
        set_role(user, role)
    return get_user_model().objects.get(pk=user.pk)


def make_product(sku="TEST-001", name="Test Product", price="100.00", reorder=5, **extra):
    return Product.objects.create(
        name=name, sku=sku, unit_price=price, reorder_level=reorder, **extra
    )


def make_supplier(code="SUP-T1", name="Test Supplier", **extra):
    return Supplier.objects.create(name=name, code=code, **extra)


def receive(product, actor, quantity, **extra):
    return record_stock_movement(
        product_id=product.pk,
        actor=actor,
        kind="receive",
        quantity=quantity,
        reason="Test receipt",
        operation_id=uuid4(),
        **extra,
    )

"""Transactional stock operations."""

from dataclasses import dataclass
from uuid import UUID

from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import IntegrityError, connection, transaction

from .models import (
    MAX_STOCK_QUANTITY,
    Product,
    StockBalance,
    StockMovement,
)


class StockError(ValueError):
    """A stock request violates a business rule."""


@dataclass(frozen=True)
class StockResult:
    movement: StockMovement
    created: bool


def existing_result(
    movement,
    *,
    product_id,
    actor_id,
    kind,
    quantity,
    reason,
):
    """Accept a retry only when it matches the original operation."""
    original = (
        movement.product_id,
        movement.actor_id,
        movement.kind,
        movement.quantity,
        movement.reason,
    )
    submitted = (
        product_id,
        actor_id,
        kind,
        quantity,
        reason,
    )

    if original != submitted:
        raise StockError(
            "This operation ID already belongs to a different stock request."
        )

    return StockResult(movement=movement, created=False)


def record_stock_movement(
    *,
    product_id,
    actor,
    kind,
    quantity,
    reason,
    operation_id,
):
    """Record one stock operation safely, including duplicate retries."""
    permissions = {
        StockMovement.Kind.RECEIVE: "inventory.receive_stock",
        StockMovement.Kind.ISSUE: "inventory.issue_stock",
    }

    if kind not in permissions:
        raise StockError("Choose a valid stock operation.")

    if (
        not getattr(actor, "is_authenticated", False)
        or not getattr(actor, "is_active", False)
        or actor.pk is None
    ):
        raise PermissionDenied("An active account is required.")

    if not actor.has_perms(
        ["inventory.view_product", permissions[kind]]
    ):
        raise PermissionDenied("You cannot perform this stock operation.")

    if type(product_id) is not int or product_id <= 0:
        raise StockError("Choose a valid product.")

    if (
        type(quantity) is not int
        or quantity <= 0
        or quantity > MAX_STOCK_QUANTITY
    ):
        raise StockError("Quantity must be a positive whole number within range.")

    if not isinstance(reason, str):
        raise StockError("Provide a reason for this stock operation.")

    reason = reason.strip()

    if not reason or len(reason) > 500:
        raise StockError("Reason must contain between 1 and 500 characters.")

    if not isinstance(operation_id, UUID):
        raise StockError("A valid operation ID is required.")

    if connection.vendor != "postgresql":
        raise ImproperlyConfigured(
            "Stock operations require PostgreSQL for row locking."
        )

    request_details = {
        "product_id": product_id,
        "actor_id": actor.pk,
        "kind": kind,
        "quantity": quantity,
        "reason": reason,
    }

    try:
        with transaction.atomic():
            product = Product.objects.select_for_update().get(
                pk=product_id
            )

            existing = StockMovement.objects.filter(
                operation_id=operation_id
            ).first()

            if existing is not None:
                return existing_result(existing, **request_details)

            if not product.is_active:
                raise StockError(
                    "Restore this product before recording stock movements."
                )

            balance, _ = StockBalance.objects.get_or_create(
                product=product
            )
            before = balance.quantity

            if kind == StockMovement.Kind.RECEIVE:
                after = before + quantity

                if after > MAX_STOCK_QUANTITY:
                    raise StockError(
                        "This receipt would exceed the supported stock limit."
                    )
            else:
                if quantity > before:
                    raise StockError(
                        "The issue quantity exceeds the available stock."
                    )

                after = before - quantity

            balance.quantity = after
            balance.save(update_fields=["quantity", "updated_at"])

            movement = StockMovement.objects.create(
                product=product,
                actor=actor,
                operation_id=operation_id,
                kind=kind,
                quantity=quantity,
                reason=reason,
                balance_before=before,
                balance_after=after,
            )

            return StockResult(movement=movement, created=True)

    except IntegrityError:
        # The failed transaction has rolled back before this query runs.
        # Another product's simultaneous request may have used the same ID.
        existing = StockMovement.objects.filter(
            operation_id=operation_id
        ).first()

        if existing is None:
            raise

        return existing_result(existing, **request_details)
"""Transactional stock operations."""

from dataclasses import dataclass
from uuid import UUID

from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import IntegrityError, connection, transaction

from .models import (
    MAX_STOCK_QUANTITY,
    Product,
    PurchaseOrder,
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
    purchase_order_id=None,
    reversal_of_id=None,
    document_reference="",
    counted_quantity=None,
):
    """Accept a retry only when it matches the original operation."""
    original = (
        movement.product_id,
        movement.actor_id,
        movement.kind,
        movement.quantity,
        movement.reason,
        movement.purchase_order_id,
        movement.reversal_of_id,
        movement.document_reference,
        movement.counted_quantity,
    )
    submitted = (
        product_id,
        actor_id,
        kind,
        quantity,
        reason,
        purchase_order_id,
        reversal_of_id,
        document_reference,
        counted_quantity,
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
    purchase_order_id=None,
    reversal_of_id=None,
    document_reference="",
    counted_quantity=None,
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

    if not actor.has_perms(["inventory.view_product", permissions[kind]]):
        raise PermissionDenied("You cannot perform this stock operation.")

    if type(product_id) is not int or product_id <= 0:
        raise StockError("Choose a valid product.")

    if type(quantity) is not int or quantity <= 0 or quantity > MAX_STOCK_QUANTITY:
        raise StockError("Quantity must be a positive whole number within range.")

    if not isinstance(reason, str):
        raise StockError("Provide a reason for this stock operation.")

    reason = reason.strip()

    if not reason or len(reason) > 500:
        raise StockError("Reason must contain between 1 and 500 characters.")

    if not isinstance(operation_id, UUID):
        raise StockError("A valid operation ID is required.")

    if not isinstance(document_reference, str) or len(document_reference.strip()) > 100:
        raise StockError("Document reference must contain at most 100 characters.")
    document_reference = document_reference.strip()
    for identifier in [purchase_order_id, reversal_of_id]:
        if identifier is not None and (type(identifier) is not int or identifier <= 0):
            raise StockError("Invalid linked document.")
    if counted_quantity is not None and (
        type(counted_quantity) is not int
        or not 0 <= counted_quantity <= MAX_STOCK_QUANTITY
    ):
        raise StockError("Count must be a whole number within range.")
    if (
        reversal_of_id is not None or counted_quantity is not None
    ) and not actor.has_perm("inventory.correct_stock"):
        raise PermissionDenied("You cannot correct stock.")

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
        "purchase_order_id": purchase_order_id,
        "reversal_of_id": reversal_of_id,
        "document_reference": document_reference,
        "counted_quantity": counted_quantity,
    }

    try:
        with transaction.atomic():
            product = Product.objects.select_for_update().get(pk=product_id)

            existing = StockMovement.objects.filter(operation_id=operation_id).first()

            if existing is not None:
                return existing_result(existing, **request_details)

            if not product.is_active:
                raise StockError(
                    "Restore this product before recording stock movements."
                )

            original = None
            if reversal_of_id is not None:
                original = StockMovement.objects.filter(
                    pk=reversal_of_id, product=product
                ).first()
                if (
                    original is None
                    or original.reversal_of_id
                    or StockMovement.objects.filter(reversal_of=original).exists()
                ):
                    raise StockError("This movement cannot be reversed again.")
                opposite = "issue" if original.kind == "receive" else "receive"
                if (
                    kind != opposite
                    or quantity != original.quantity
                    or purchase_order_id != original.purchase_order_id
                    or counted_quantity is not None
                ):
                    raise StockError(
                        "A reversal must exactly offset its original movement."
                    )

            order = None
            if purchase_order_id is not None:
                if not actor.has_perms(
                    [
                        "inventory.view_purchaseorder",
                        "inventory.change_purchaseorder",
                        "suppliers.view_supplier",
                    ]
                ):
                    raise PermissionDenied("You cannot receive purchase orders.")
                order = (
                    PurchaseOrder.objects.select_for_update()
                    .filter(pk=purchase_order_id, product=product)
                    .first()
                )
                if order is None:
                    raise StockError("Choose a purchase order for this product.")
                from suppliers.models import Supplier

                supplier = Supplier.objects.select_for_update().get(
                    pk=order.supplier_id
                )
                if original is None:
                    if (
                        kind != "receive"
                        or order.cancelled
                        or quantity > order.outstanding
                    ):
                        raise StockError(
                            "This receipt exceeds the outstanding order or the order is cancelled."
                        )
                    if not document_reference:
                        raise StockError("A delivery document reference is required.")
                    if not supplier.is_active:
                        raise StockError(
                            "Restore the supplier before receiving this order."
                        )
                if original is not None and original.kind != "receive":
                    raise StockError("A purchase reversal cannot itself be reversed.")

            balance, _ = StockBalance.objects.get_or_create(product=product)
            before = balance.quantity

            if kind == StockMovement.Kind.RECEIVE:
                after = before + quantity

                if after > MAX_STOCK_QUANTITY:
                    raise StockError(
                        "This receipt would exceed the supported stock limit."
                    )
            else:
                if quantity > before:
                    raise StockError("The issue quantity exceeds the available stock.")

                after = before - quantity

            if counted_quantity is not None and (
                original is not None or order is not None or after != counted_quantity
            ):
                raise StockError(
                    "The stocktake adjustment does not match the counted stock."
                )

            if order is not None:
                order.received_quantity += -quantity if original else quantity
                order.save(update_fields=["received_quantity"])

            balance.quantity = after
            balance.save(update_fields=["quantity", "updated_at"])

            movement = StockMovement.objects.create(
                product=product,
                actor=actor,
                operation_id=operation_id,
                kind=kind,
                quantity=quantity,
                reason=reason,
                purchase_order=order,
                reversal_of=original,
                document_reference=document_reference,
                unit_cost=original.unit_cost
                if original
                else (order.unit_cost if order else None),
                counted_quantity=counted_quantity,
                balance_before=before,
                balance_after=after,
            )

            return StockResult(movement=movement, created=True)

    except IntegrityError:
        # The failed transaction has rolled back before this query runs.
        # Another product's simultaneous request may have used the same ID.
        existing = StockMovement.objects.filter(operation_id=operation_id).first()

        if existing is None:
            raise

        return existing_result(existing, **request_details)


def reverse_movement(*, movement_id, actor, reason, operation_id):
    """Append an exact opposite movement, preserving the original record."""
    original = StockMovement.objects.get(pk=movement_id)
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=original.product_id)
        return record_stock_movement(
            product_id=original.product_id,
            actor=actor,
            kind="issue" if original.kind == "receive" else "receive",
            quantity=original.quantity,
            reason=reason,
            operation_id=operation_id,
            reversal_of_id=original.pk,
            purchase_order_id=original.purchase_order_id,
        )


def record_stocktake(*, product_id, actor, counted_quantity, reason, operation_id):
    """Compare a physical count with the balance under its product lock."""
    if (
        type(counted_quantity) is not int
        or not 0 <= counted_quantity <= MAX_STOCK_QUANTITY
    ):
        raise StockError("Count must be a whole number within range.")
    if (
        not getattr(actor, "is_authenticated", False)
        or not getattr(actor, "is_active", False)
        or getattr(actor, "pk", None) is None
        or not actor.has_perm("inventory.correct_stock")
    ):
        raise PermissionDenied("You cannot record a stocktake.")
    if not isinstance(operation_id, UUID):
        raise StockError("A valid operation ID is required.")
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=product_id)
        existing = StockMovement.objects.filter(operation_id=operation_id).first()
        before = (
            StockBalance.objects.filter(product_id=product_id)
            .values_list("quantity", flat=True)
            .first()
            or 0
        )
        if existing:
            kind, quantity = existing.kind, existing.quantity
        else:
            if before == counted_quantity:
                raise StockError(
                    "The count already matches stock. No adjustment is needed."
                )
            kind = "receive" if counted_quantity > before else "issue"
            quantity = abs(counted_quantity - before)
        return record_stock_movement(
            product_id=product_id,
            actor=actor,
            kind=kind,
            quantity=quantity,
            reason=reason,
            operation_id=operation_id,
            counted_quantity=counted_quantity,
        )
"""Sales order workflow. Stock leaves only through the inventory service."""

from uuid import NAMESPACE_URL, uuid5

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from inventory.models import Product
from inventory.services import StockError, record_stock_movement

from .models import SalesOrder


class SalesError(ValueError):
    """A sales order request violates a business rule."""


def next_order_number():
    """SO-YYYY-00001 style numbers, sequential per year."""
    prefix = f"SO-{timezone.localdate():%Y}-"
    last = (
        SalesOrder.objects.filter(number__startswith=prefix)
        .aggregate(last=Max("number"))["last"]
    )
    sequence = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{sequence:05d}"


def line_operation_id(order, line):
    """Deterministic ID so a retried fulfilment never issues stock twice."""
    return uuid5(NAMESPACE_URL, f"stockflow:sales-order:{order.pk}:line:{line.pk}")


def _locked(order_id):
    return SalesOrder.objects.select_for_update().get(pk=order_id)


def confirm_order(*, order_id, actor):
    if not actor.has_perm("sales.change_salesorder"):
        raise PermissionDenied("You cannot confirm sales orders.")
    with transaction.atomic():
        order = _locked(order_id)
        if order.status != SalesOrder.Status.DRAFT:
            raise SalesError("Only draft orders can be confirmed.")
        if not order.lines.exists():
            raise SalesError("Add at least one line before confirming.")
        order.status = SalesOrder.Status.CONFIRMED
        order.save(update_fields=["status", "updated_at"])
        return order


def cancel_order(*, order_id, actor):
    if not actor.has_perm("sales.change_salesorder"):
        raise PermissionDenied("You cannot cancel sales orders.")
    with transaction.atomic():
        order = _locked(order_id)
        if order.status not in {SalesOrder.Status.DRAFT, SalesOrder.Status.CONFIRMED}:
            raise SalesError("Fulfilled or cancelled orders cannot be cancelled.")
        order.status = SalesOrder.Status.CANCELLED
        order.save(update_fields=["status", "updated_at"])
        return order


def fulfil_order(*, order_id, actor):
    """Issue every line atomically; any shortage rolls the whole order back."""
    if not actor.has_perm("sales.fulfil_salesorder"):
        raise PermissionDenied("You cannot fulfil sales orders.")
    with transaction.atomic():
        order = _locked(order_id)
        if order.status == SalesOrder.Status.FULFILLED:
            return order
        if order.status != SalesOrder.Status.CONFIRMED:
            raise SalesError("Confirm the order before fulfilling it.")

        lines = list(order.lines.select_related("product").order_by("product_id"))
        if not lines:
            raise SalesError("This order has no lines.")

        # Lock products in a stable order to avoid deadlocks with other orders.
        list(
            Product.objects.select_for_update()
            .filter(pk__in=[line.product_id for line in lines])
            .order_by("pk")
        )

        for line in lines:
            try:
                result = record_stock_movement(
                    product_id=line.product_id,
                    actor=actor,
                    kind="issue",
                    quantity=int(line.quantity),
                    reason=f"Sales order {order.number} for {order.customer.name}",
                    operation_id=line_operation_id(order, line),
                    document_reference=order.number,
                )
            except StockError as error:
                raise SalesError(f"{line.product.name}: {error}") from error
            line.movement = result.movement
            line.save(update_fields=["movement"])

        order.status = SalesOrder.Status.FULFILLED
        order.fulfilled_by = actor
        order.fulfilled_at = timezone.now()
        order.save(update_fields=["status", "fulfilled_by", "fulfilled_at", "updated_at"])
        return order

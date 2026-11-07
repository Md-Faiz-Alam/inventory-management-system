"""Purchase orders, receipts against them, reversals, and stocktakes."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from audit.log import history_for, record
from audit.models import AuditEntry
from config.htmx import render_list
from config.pdf import render_pdf

from .forms import CorrectionForm, PurchaseOrderForm, PurchaseReceiptForm, StocktakeForm
from .models import PurchaseOrder, StockMovement
from .services import StockError, record_stock_movement, record_stocktake, reverse_movement
from .views import pagination_query, products_with_stock, workspace_context

PO_STATUSES = {"open", "complete", "cancelled", "all"}


def suggested_reference():
    prefix = f"PO-{timezone.localdate():%Y%m%d}-"
    count = PurchaseOrder.objects.filter(reference__startswith=prefix).count()
    return f"{prefix}{count + 1:03d}"


@never_cache
@login_required(login_url="login")
@permission_required(
    ["inventory.view_purchaseorder", "inventory.view_product"], raise_exception=True
)
def po_list(request):
    query = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "open")
    if status not in PO_STATUSES:
        status = "open"

    orders = PurchaseOrder.objects.select_related("supplier", "product")
    if status == "open":
        orders = orders.filter(cancelled=False).exclude(
            received_quantity=F("quantity")
        )
    elif status == "complete":
        orders = orders.filter(cancelled=False, received_quantity=F("quantity"))
    elif status == "cancelled":
        orders = orders.filter(cancelled=True)
    if query:
        orders = orders.filter(
            Q(reference__icontains=query)
            | Q(supplier__name__icontains=query)
            | Q(product__name__icontains=query)
            | Q(product__sku__icontains=query)
        )

    context = workspace_context(request)
    context.update(
        {
            "active_page": "purchasing",
            "page_obj": Paginator(orders, 15).get_page(request.GET.get("page")),
            "query": query,
            "selected_status": status,
            "pagination_query": pagination_query(request),
        }
    )
    return render_list(
        request, "inventory/po_list.html", "inventory/_po_rows.html", context
    )


@never_cache
@login_required(login_url="login")
@permission_required(
    [
        "inventory.view_purchaseorder",
        "inventory.add_purchaseorder",
        "inventory.view_product",
        "suppliers.view_supplier",
    ],
    raise_exception=True,
)
@require_http_methods(["GET", "POST"])
def po_create(request):
    initial = {"reference": suggested_reference()}
    for key in ("product", "supplier"):
        if request.GET.get(key, "").isdigit():
            initial[key] = request.GET[key]
    if "product" in initial and "unit_cost" not in initial:
        product = products_with_stock().filter(pk=initial["product"]).first()
        if product:
            initial["unit_cost"] = product.unit_price
            initial["quantity"] = max(product.reorder_level * 2 - product.on_hand, 1)
            supplier = product.suppliers.filter(is_active=True).first()
            if supplier and "supplier" not in initial:
                initial["supplier"] = supplier.pk

    form = PurchaseOrderForm(
        request.POST if request.method == "POST" else None, initial=initial
    )
    if request.method == "POST" and form.is_valid():
        order = form.save(commit=False)
        order.actor = request.user
        try:
            order.save()
        except IntegrityError:
            form.add_error("reference", "This order reference already exists.")
        else:
            record(
                request,
                AuditEntry.Action.CREATE,
                f"Created purchase order {order.reference} "
                f"for {order.quantity} × {order.product.name}",
                obj=order,
            )
            messages.success(request, f"Purchase order {order.reference} was created.")
            return redirect("po-detail", pk=order.pk)

    context = workspace_context(request)
    context.update(
        {"active_page": "purchasing", "form": form, "page_title": "New purchase order"}
    )
    return render(request, "inventory/po_form.html", context)


@never_cache
@login_required(login_url="login")
@permission_required(
    ["inventory.view_purchaseorder", "inventory.view_product"], raise_exception=True
)
def po_detail(request, pk):
    order = get_object_or_404(
        PurchaseOrder.objects.select_related("supplier", "product", "actor"), pk=pk
    )
    context = workspace_context(request)
    context.update(
        {
            "active_page": "purchasing",
            "order": order,
            "receipts": order.movements.select_related("actor", "reversal_of").order_by(
                "created_at", "pk"
            ),
            "history": history_for(order),
            "order_value": order.quantity * order.unit_cost,
            "progress": int(order.received_quantity * 100 / order.quantity)
            if order.quantity
            else 0,
        }
    )
    return render(request, "inventory/po_detail.html", context)


@never_cache
@login_required(login_url="login")
@permission_required(
    ["inventory.view_purchaseorder", "inventory.view_product"], raise_exception=True
)
def po_pdf(request, pk):
    order = get_object_or_404(
        PurchaseOrder.objects.select_related("supplier", "product", "actor"), pk=pk
    )
    return render_pdf(
        request,
        "pdf/purchase_order.html",
        {
            "order": order,
            "order_value": order.quantity * order.unit_cost,
            "receipts": order.movements.order_by("created_at", "pk"),
        },
        f"{order.reference}.pdf",
    )


@never_cache
@login_required(login_url="login")
@permission_required(
    [
        "inventory.view_purchaseorder",
        "inventory.change_purchaseorder",
        "inventory.view_product",
        "inventory.receive_stock",
        "suppliers.view_supplier",
    ],
    raise_exception=True,
)
@require_http_methods(["GET", "POST"])
def po_receive(request, pk):
    order = get_object_or_404(
        PurchaseOrder.objects.select_related("supplier", "product"), pk=pk
    )
    form = PurchaseReceiptForm(
        request.POST if request.method == "POST" else None,
        initial={"quantity": order.outstanding or None},
    )
    if request.method == "POST" and form.is_valid():
        try:
            result = record_stock_movement(
                product_id=order.product_id,
                actor=request.user,
                kind="receive",
                quantity=form.cleaned_data["quantity"],
                reason=form.cleaned_data["reason"],
                operation_id=form.cleaned_data["operation_id"],
                purchase_order_id=order.pk,
                document_reference=form.cleaned_data["document_reference"],
            )
        except StockError as error:
            form.add_error(None, str(error))
        else:
            if result.created:
                record(
                    request,
                    AuditEntry.Action.STOCK,
                    f"Received {result.movement.quantity} units against {order.reference}",
                    obj=order,
                )
                messages.success(
                    request,
                    f"Received {result.movement.quantity} units against {order.reference}.",
                )
            else:
                messages.info(request, "This receipt was already recorded.")
            return redirect("po-detail", pk=order.pk)

    context = workspace_context(request)
    context.update({"active_page": "purchasing", "order": order, "form": form})
    return render(request, "inventory/po_receive.html", context)


@login_required(login_url="login")
@permission_required(
    ["inventory.view_purchaseorder", "inventory.change_purchaseorder"],
    raise_exception=True,
)
@require_POST
def po_cancel(request, pk):
    with transaction.atomic():
        order = get_object_or_404(PurchaseOrder.objects.select_for_update(), pk=pk)
        if order.cancelled or order.outstanding == 0:
            messages.error(request, "Only open orders can be cancelled.")
            return redirect("po-detail", pk=pk)
        order.cancelled = True
        order.save(update_fields=["cancelled"])
    record(
        request,
        AuditEntry.Action.STATUS,
        f"Cancelled purchase order {order.reference}",
        obj=order,
    )
    messages.success(
        request,
        f"{order.reference} was cancelled. Received stock stays on hand.",
    )
    return redirect("po-detail", pk=pk)


@never_cache
@login_required(login_url="login")
@permission_required(
    [
        "inventory.view_product",
        "inventory.view_stockmovement",
        "inventory.correct_stock",
    ],
    raise_exception=True,
)
@require_http_methods(["GET", "POST"])
def movement_reverse(request, pk):
    movement = get_object_or_404(
        StockMovement.objects.select_related("product", "actor", "purchase_order"),
        pk=pk,
    )
    blocked = None
    if movement.reversal_of_id:
        blocked = "This movement is itself a reversal and cannot be reversed."
    elif StockMovement.objects.filter(reversal_of=movement).exists():
        blocked = "This movement has already been reversed."
    elif movement.counted_quantity is not None:
        blocked = "Stocktake adjustments are corrected with a new stocktake."
    elif StockMovement.objects.filter(pk=movement.pk, sales_line__isnull=False).exists():
        blocked = (
            "This issue fulfilled a sales order, which would still show as fulfilled. "
            "Record returned goods with Receive stock and a reason such as "
            "'Customer return for SO-…'."
        )

    form = CorrectionForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and blocked is None and form.is_valid():
        try:
            result = reverse_movement(
                movement_id=movement.pk,
                actor=request.user,
                reason=form.cleaned_data["reason"],
                operation_id=form.cleaned_data["operation_id"],
            )
        except StockError as error:
            form.add_error(None, str(error))
        else:
            if result.created:
                record(
                    request,
                    AuditEntry.Action.STOCK,
                    f"Reversed movement #{movement.pk} for {movement.product.name}",
                    obj=movement.product,
                )
            messages.success(request, f"Movement #{movement.pk} was reversed.")
            return redirect("movement-list")

    context = workspace_context(request)
    context.update(
        {
            "active_page": "movements",
            "movement": movement,
            "form": form,
            "blocked": blocked,
        }
    )
    return render(request, "inventory/movement_reverse.html", context)


@never_cache
@login_required(login_url="login")
@permission_required(
    ["inventory.view_product", "inventory.correct_stock"], raise_exception=True
)
@require_http_methods(["GET", "POST"])
def stocktake(request, pk):
    product = get_object_or_404(products_with_stock(), pk=pk)
    form = StocktakeForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            result = record_stocktake(
                product_id=product.pk,
                actor=request.user,
                counted_quantity=form.cleaned_data["counted_quantity"],
                reason=form.cleaned_data["reason"],
                operation_id=form.cleaned_data["operation_id"],
            )
        except StockError as error:
            form.add_error(None, str(error))
        else:
            movement = result.movement
            if result.created:
                record(
                    request,
                    AuditEntry.Action.STOCK,
                    f"Stocktake for {product.name}: "
                    f"{movement.balance_before} → {movement.balance_after}",
                    obj=product,
                )
            messages.success(
                request,
                f"Stock for {product.name} adjusted to {movement.balance_after}.",
            )
            return redirect("stock-list")

    context = workspace_context(request)
    context.update({"active_page": "stock", "product": product, "form": form})
    return render(request, "inventory/stocktake.html", context)

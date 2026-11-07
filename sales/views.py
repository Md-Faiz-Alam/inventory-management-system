"""Customer directory and sales order pages."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from audit.log import diff, history_for, record, snapshot
from audit.models import AuditEntry
from config.htmx import render_list
from config.pdf import render_pdf
from inventory.views import pagination_query, products_with_stock, workspace_context

from .forms import (
    CUSTOMER_FIELDS,
    CustomerForm,
    SalesFilterForm,
    SalesOrderForm,
    SalesOrderLineFormSet,
)
from .models import Customer, SalesOrder
from .services import (
    SalesError,
    cancel_order,
    confirm_order,
    fulfil_order,
    next_order_number,
)

ORDER_TOTAL = Coalesce(
    Sum(
        ExpressionWrapper(
            F("lines__quantity") * F("lines__unit_price"),
            output_field=DecimalField(max_digits=24, decimal_places=2),
        )
    ),
    0,
    output_field=DecimalField(max_digits=24, decimal_places=2),
)


# ---------------------------------------------------------------- customers


@never_cache
@login_required(login_url="login")
@permission_required("sales.view_customer", raise_exception=True)
def customer_list(request):
    query = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "active")
    if status not in {"active", "archived", "all"}:
        status = "active"

    customers = Customer.objects.all()
    if status != "all":
        customers = customers.filter(is_active=status == "active")
    if query:
        customers = customers.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
        )

    context = workspace_context(request)
    context.update(
        {
            "active_page": "customers",
            "page_obj": Paginator(customers, 12).get_page(request.GET.get("page")),
            "query": query,
            "selected_status": status,
            "pagination_query": pagination_query(request),
        }
    )
    return render_list(
        request, "sales/customer_list.html", "sales/_customer_rows.html", context
    )


@never_cache
@login_required(login_url="login")
@permission_required("sales.view_customer", raise_exception=True)
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    context = workspace_context(request)
    context.update(
        {
            "active_page": "customers",
            "customer": customer,
            "history": history_for(customer),
        }
    )
    if request.user.has_perm("sales.view_salesorder"):
        context["orders"] = customer.orders.annotate(order_total=ORDER_TOTAL).order_by(
            "-created_at", "-pk"
        )[:10]
    return render(request, "sales/customer_detail.html", context)


@never_cache
@login_required(login_url="login")
@permission_required("sales.view_customer", raise_exception=True)
@require_http_methods(["GET", "POST"])
def customer_edit(request, pk=None):
    permission = "sales.change_customer" if pk else "sales.add_customer"
    if not request.user.has_perm(permission):
        raise PermissionDenied("You cannot edit customers.")

    customer = get_object_or_404(Customer, pk=pk) if pk else None
    before = snapshot(customer, CUSTOMER_FIELDS) if customer else {}
    form = CustomerForm(
        request.POST if request.method == "POST" else None, instance=customer
    )

    if request.method == "POST" and form.is_valid():
        saved = None
        try:
            with transaction.atomic():
                obj = form.save(commit=False)
                if obj.pk:
                    current = Customer.objects.select_for_update().get(pk=obj.pk)
                    if form.matches_current(current):
                        obj.save(update_fields=[*CUSTOMER_FIELDS, "updated_at"])
                        saved = obj
                else:
                    obj.save()
                    saved = obj
        except IntegrityError:
            form.add_error("code", "This customer code was just taken. Choose another.")

        if saved is not None:
            after = snapshot(saved, CUSTOMER_FIELDS)
            record(
                request,
                AuditEntry.Action.UPDATE if pk else AuditEntry.Action.CREATE,
                f"{'Updated' if pk else 'Created'} customer {saved.name}",
                obj=saved,
                changes=diff(before, after),
            )
            messages.success(request, f"{saved.name} was {'updated' if pk else 'created'}.")
            return redirect("customer-detail", pk=saved.pk)

    context = workspace_context(request)
    context.update(
        {
            "active_page": "customers",
            "form": form,
            "page_title": "Edit customer" if pk else "Add customer",
            "submit_label": "Save changes" if pk else "Create customer",
            "cancel_url": "customer-list",
        }
    )
    return render(request, "sales/customer_form.html", context)


@login_required(login_url="login")
@permission_required(
    ["sales.view_customer", "sales.change_customer"], raise_exception=True
)
@require_POST
def customer_status(request, pk):
    action = request.POST.get("action")
    if action not in {"archive", "restore"}:
        return HttpResponseBadRequest("Invalid customer action.")
    with transaction.atomic():
        customer = get_object_or_404(Customer.objects.select_for_update(), pk=pk)
        customer.is_active = action == "restore"
        customer.save(update_fields=["is_active", "updated_at"])
    record(
        request,
        AuditEntry.Action.STATUS,
        f"{action.title()}d customer {customer.name}",
        obj=customer,
    )
    messages.success(request, f"{customer.name} was {action}d.")
    return redirect("customer-detail", pk=pk)


# ------------------------------------------------------------------- orders


@never_cache
@login_required(login_url="login")
@permission_required("sales.view_salesorder", raise_exception=True)
def order_list(request):
    form = SalesFilterForm(request.GET)
    # Totals use GROUP BY, which drops the model's default ordering, so order explicitly
    # to keep pagination stable.
    orders = (
        SalesOrder.objects.select_related("customer")
        .annotate(order_total=ORDER_TOTAL)
        .order_by("-created_at", "-pk")
    )
    if form.is_valid():
        if form.cleaned_data["q"]:
            query = form.cleaned_data["q"]
            orders = orders.filter(
                Q(number__icontains=query)
                | Q(customer__name__icontains=query)
                | Q(customer__code__icontains=query)
            )
        if form.cleaned_data["status"]:
            orders = orders.filter(status=form.cleaned_data["status"])

    context = workspace_context(request)
    context.update(
        {
            "active_page": "sales",
            "filter_form": form,
            "page_obj": Paginator(orders, 15).get_page(request.GET.get("page")),
            "pagination_query": pagination_query(request),
        }
    )
    return render_list(
        request, "sales/order_list.html", "sales/_order_rows.html", context
    )


def _save_order(request, order, form, formset, creating):
    for attempt in range(3):
        try:
            with transaction.atomic():
                obj = form.save(commit=False)
                if creating:
                    obj.number = next_order_number()
                    obj.actor = request.user
                else:
                    locked = SalesOrder.objects.select_for_update().get(pk=obj.pk)
                    if not locked.is_editable:
                        raise SalesError("This order is no longer a draft.")
                obj.save()
                formset.instance = obj
                formset.save()
                return obj
        except IntegrityError:
            if not creating or attempt == 2:
                raise
    return None


@never_cache
@login_required(login_url="login")
@permission_required(
    ["sales.view_salesorder", "inventory.view_product"], raise_exception=True
)
@require_http_methods(["GET", "POST"])
def order_edit(request, pk=None):
    creating = pk is None
    permission = "sales.add_salesorder" if creating else "sales.change_salesorder"
    if not request.user.has_perm(permission):
        raise PermissionDenied("You cannot edit sales orders.")

    order = None if creating else get_object_or_404(SalesOrder, pk=pk)
    if order is not None and not order.is_editable:
        messages.error(request, "Only draft orders can be edited.")
        return redirect("order-detail", pk=order.pk)

    data = request.POST if request.method == "POST" else None
    initial = {}
    if creating and request.GET.get("customer", "").isdigit():
        initial["customer"] = request.GET["customer"]
    form = SalesOrderForm(data, instance=order, initial=initial)
    formset = SalesOrderLineFormSet(
        data, instance=order or SalesOrder(), prefix="lines"
    )

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        try:
            saved = _save_order(request, order, form, formset, creating)
        except SalesError as error:
            messages.error(request, str(error))
            return redirect("order-detail", pk=pk)
        record(
            request,
            AuditEntry.Action.CREATE if creating else AuditEntry.Action.UPDATE,
            f"{'Created' if creating else 'Updated'} sales order {saved.number}",
            obj=saved,
        )
        messages.success(
            request, f"Sales order {saved.number} was {'created' if creating else 'updated'}."
        )
        return redirect("order-detail", pk=saved.pk)

    context = workspace_context(request)
    context.update(
        {
            "active_page": "sales",
            "form": form,
            "formset": formset,
            "order": order,
            "page_title": "New sales order" if creating else f"Edit {order.number}",
            "product_info": {
                p["pk"]: {"price": str(p["unit_price"]), "stock": p["on_hand"]}
                for p in products_with_stock()
                .filter(is_active=True)
                .values("pk", "unit_price", "on_hand")
            },
        }
    )
    return render(request, "sales/order_form.html", context)


@never_cache
@login_required(login_url="login")
@permission_required("sales.view_salesorder", raise_exception=True)
def order_detail(request, pk):
    order = get_object_or_404(
        SalesOrder.objects.select_related("customer", "actor", "fulfilled_by"), pk=pk
    )
    lines = list(
        order.lines.select_related("product", "product__stock_balance", "movement")
    )
    context = workspace_context(request)
    context.update(
        {
            "active_page": "sales",
            "order": order,
            "lines": lines,
            "order_total": sum((line.line_total for line in lines), 0),
            "history": history_for(order),
        }
    )
    return render(request, "sales/order_detail.html", context)


@never_cache
@login_required(login_url="login")
@permission_required("sales.view_salesorder", raise_exception=True)
def order_invoice(request, pk):
    order = get_object_or_404(SalesOrder.objects.select_related("customer"), pk=pk)
    lines = list(order.lines.select_related("product"))
    kind = "invoice" if order.status == SalesOrder.Status.FULFILLED else "quotation"
    return render_pdf(
        request,
        "pdf/invoice.html",
        {
            "order": order,
            "lines": lines,
            "order_total": sum((line.line_total for line in lines), 0),
        },
        f"{order.number}-{kind}.pdf",
    )


@login_required(login_url="login")
@permission_required("sales.view_salesorder", raise_exception=True)
@require_POST
def order_action(request, pk):
    action = request.POST.get("action")
    handlers = {"confirm": confirm_order, "fulfil": fulfil_order, "cancel": cancel_order}
    if action not in handlers:
        return HttpResponseBadRequest("Invalid order action.")
    try:
        order = handlers[action](order_id=pk, actor=request.user)
    except SalesError as error:
        messages.error(request, str(error))
    else:
        past = {"confirm": "confirmed", "fulfil": "fulfilled", "cancel": "cancelled"}[action]
        record(
            request,
            AuditEntry.Action.ORDER,
            f"Sales order {order.number} {past}",
            obj=order,
        )
        messages.success(request, f"Sales order {order.number} was {past}.")
    return redirect("order-detail", pk=pk)

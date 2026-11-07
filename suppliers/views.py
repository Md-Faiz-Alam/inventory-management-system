"""Permission-protected supplier directory views."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from audit.log import diff, history_for, record, snapshot
from audit.models import AuditEntry
from config.htmx import render_list
from inventory.views import pagination_query, workspace_context

from .forms import SUPPLIER_DETAIL_FIELDS, SupplierForm
from .models import Supplier


def save_supplier_form(form):
    """Save contact details and permitted product links atomically."""
    try:
        with transaction.atomic():
            supplier = form.save(commit=False)

            if supplier.pk:
                current = get_object_or_404(
                    Supplier.objects.select_for_update(),
                    pk=supplier.pk,
                )
                if not form.matches_current(current):
                    return None
                supplier.save(
                    update_fields=[
                        *SUPPLIER_DETAIL_FIELDS,
                        "updated_at",
                    ]
                )
            else:
                supplier.save()

            form.save_m2m()
            return supplier

    except IntegrityError:
        duplicates = Supplier.objects.filter(
            code__iexact=form.cleaned_data["code"]
        )

        if form.instance.pk:
            duplicates = duplicates.exclude(pk=form.instance.pk)

        if not duplicates.exists():
            raise

        form.add_error(
            "code",
            "This code was assigned to another supplier. "
            "Choose a different code.",
        )
        return None


@never_cache
@login_required(login_url="login")
@permission_required("suppliers.view_supplier", raise_exception=True)
@require_http_methods(["GET"])
def supplier_list(request):
    query = request.GET.get("q", "").strip()[:100]
    status = request.GET.get("status", "active")

    if status not in {"active", "archived", "all"}:
        status = "active"

    suppliers = Supplier.objects.all()

    if status != "all":
        suppliers = suppliers.filter(is_active=status == "active")

    if query:
        suppliers = suppliers.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
            | Q(contact_name__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
        )

    if request.user.has_perm("inventory.view_product"):
        suppliers = suppliers.annotate(product_count=Count("products"))

    suppliers = suppliers.order_by("name", "pk")

    context = workspace_context(request)
    context.update(
        {
            "active_page": "suppliers",
            "page_obj": Paginator(suppliers, 12).get_page(
                request.GET.get("page")
            ),
            "query": query,
            "selected_status": status,
            "pagination_query": pagination_query(request),
        }
    )
    return render_list(
        request,
        "suppliers/supplier_list.html",
        "suppliers/_supplier_rows.html",
        context,
    )


@never_cache
@login_required(login_url="login")
@permission_required("suppliers.view_supplier", raise_exception=True)
@require_http_methods(["GET"])
def supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    context = workspace_context(request)
    context.update(
        {
            "active_page": "suppliers",
            "supplier": supplier,
            "history": history_for(supplier),
        }
    )

    if request.user.has_perm("inventory.view_purchaseorder"):
        context["purchase_orders"] = supplier.purchaseorder_set.select_related(
            "product"
        )[:8]

    if request.user.has_perm("inventory.view_product"):
        context["product_page"] = Paginator(
            supplier.products.order_by("name", "pk"),
            12,
        ).get_page(request.GET.get("page"))

    return render(request, "suppliers/supplier_detail.html", context)


@never_cache
@login_required(login_url="login")
@permission_required("suppliers.view_supplier", raise_exception=True)
@require_http_methods(["GET", "POST"])
def supplier_edit(request, pk=None):
    permission = (
        "suppliers.change_supplier"
        if pk is not None
        else "suppliers.add_supplier"
    )

    if not request.user.has_perm(permission):
        raise PermissionDenied("You cannot edit supplier records.")

    supplier = (
        get_object_or_404(Supplier, pk=pk)
        if pk is not None
        else None
    )
    before = snapshot(supplier, SUPPLIER_DETAIL_FIELDS) if supplier else {}
    form = SupplierForm(
        request.POST if request.method == "POST" else None,
        instance=supplier,
        allow_products=request.user.has_perm("inventory.view_product"),
    )

    if request.method == "POST" and form.is_valid():
        saved_supplier = save_supplier_form(form)

        if saved_supplier is not None:
            outcome = "updated" if pk is not None else "created"
            record(
                request,
                AuditEntry.Action.UPDATE if pk is not None else AuditEntry.Action.CREATE,
                f"{outcome.title()} supplier {saved_supplier.name}",
                obj=saved_supplier,
                changes=diff(before, snapshot(saved_supplier, SUPPLIER_DETAIL_FIELDS)),
            )
            messages.success(
                request,
                f"{saved_supplier.name} was {outcome}.",
            )
            return redirect(
                "supplier-detail",
                pk=saved_supplier.pk,
            )

    context = workspace_context(request)
    context.update(
        {
            "active_page": "suppliers",
            "form": form,
            "page_title": (
                "Edit supplier"
                if pk is not None
                else "Add supplier"
            ),
            "submit_label": (
                "Save changes"
                if pk is not None
                else "Create supplier"
            ),
        }
    )
    return render(request, "suppliers/supplier_form.html", context)


@never_cache
@login_required(login_url="login")
@permission_required(
    ["suppliers.view_supplier", "suppliers.change_supplier"],
    raise_exception=True,
)
@require_http_methods(["GET", "POST"])
def supplier_status(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)

    if request.method == "POST":
        action = request.POST.get("action")

        if action not in {"archive", "restore"}:
            return HttpResponseBadRequest("Invalid supplier action.")

        with transaction.atomic():
            supplier = get_object_or_404(
                Supplier.objects.select_for_update(),
                pk=pk,
            )
            supplier.is_active = action == "restore"
            supplier.save(
                update_fields=["is_active", "updated_at"]
            )

        outcome = "restored" if action == "restore" else "archived"
        record(
            request,
            AuditEntry.Action.STATUS,
            f"{outcome.title()} supplier {supplier.name}",
            obj=supplier,
        )
        messages.success(
            request,
            f"{supplier.name} was {outcome}.",
        )
        return redirect("supplier-list")

    context = workspace_context(request)
    context.update(
        {
            "active_page": "suppliers",
            "supplier": supplier,
        }
    )
    return render(request, "suppliers/supplier_status.html", context)
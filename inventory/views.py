"""Workspace, catalog, and stock operation views."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import BigIntegerField, Count, F, Q, Sum
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from accounts.roles import guest_account, is_guest, role_of
from audit.log import diff, history_for, record, snapshot
from audit.models import AuditEntry
from config.htmx import render_list

from .dashboard import dashboard_kpis, landing_figures
from .forms import MovementFilterForm, ProductForm, StockMovementForm
from .models import Product, StockBalance, StockMovement
from .services import StockError, record_stock_movement


def workspace_context(request) -> dict[str, object]:
    user = request.user
    access_label = role_of(user)

    if access_label == "Member" and user.is_staff:
        access_label = "Administration access"

    return {
        "display_name": user.first_name or user.get_username(),
        "access_label": "Guest (read-only)" if is_guest(user) else access_label,
        "is_guest": is_guest(user),
        "demo_roles": bool(getattr(settings, "DEMO_LOGINS", [])),
    }


PRODUCT_AUDIT_FIELDS = ProductForm.Meta.fields


def products_with_stock():
    """Read products and balances together, treating a missing balance as zero."""
    return Product.objects.annotate(
        on_hand=Coalesce(
            "stock_balance__quantity",
            0,
            output_field=BigIntegerField(),
        )
    )


def active_stock_totals():
    """Report active stock with separate low and empty categories."""
    return products_with_stock().filter(is_active=True).aggregate(
        products=Count("pk"),
        units=Sum("on_hand", default=0),
        low=Count(
            "pk",
            filter=Q(
                on_hand__gt=0,
                on_hand__lte=F("reorder_level"),
            ),
        ),
        out=Count("pk", filter=Q(on_hand=0)),
    )


def pagination_query(request):
    parameters = request.GET.copy()
    parameters.pop("page", None)
    return parameters.urlencode()


def save_product_form(form):
    """Save catalog fields without overwriting a concurrent status change."""
    try:
        with transaction.atomic():
            product = form.save(commit=False)

            if product.pk:
                current = get_object_or_404(Product.objects.select_for_update(), pk=product.pk)
                if not form.matches_current(current):
                    return None
                product.save(
                    update_fields=[*ProductForm.Meta.fields, "updated_at"]
                )
            else:
                product.save()

            return product
    except IntegrityError:
        duplicates = Product.objects.filter(
            sku__iexact=form.cleaned_data["sku"]
        )

        if form.instance.pk:
            duplicates = duplicates.exclude(pk=form.instance.pk)

        if not duplicates.exists():
            raise

        form.add_error(
            "sku",
            "This SKU was assigned to another product. Choose a different SKU.",
        )
        return None


@never_cache
def overview(request):
    if not request.user.is_authenticated:
        return render(
            request,
            "landing.html",
            {
                "demo_accounts": getattr(settings, "DEMO_LOGINS", []),
                "guest_access": guest_account() is not None,
                "showcase": landing_figures(),
            },
        )

    context = workspace_context(request)
    context["active_page"] = "overview"
    context.update(dashboard_kpis(request.user))

    if request.user.has_perm("inventory.view_product"):
        context["catalog_totals"] = Product.objects.aggregate(
            total=Count("pk"),
            active=Count("pk", filter=Q(is_active=True)),
            archived=Count("pk", filter=Q(is_active=False)),
        )
        context["stock_totals"] = active_stock_totals()
        context["recent_products"] = Product.objects.order_by(
            "-updated_at", "-pk"
        )[:5]
        context["attention_products"] = (
            products_with_stock()
            .filter(
                is_active=True,
                on_hand__lte=F("reorder_level"),
            )
            .order_by("on_hand", "name", "pk")[:5]
        )

        if request.user.has_perm("inventory.view_stockmovement"):
            context["recent_movements"] = (
                StockMovement.objects.select_related("product", "actor")[:5]
            )

    return render(request, "inventory/overview.html", context)


@never_cache
@login_required(login_url="login")
@permission_required("inventory.view_product", raise_exception=True)
def product_list(request):
    query = request.GET.get("q", "").strip()[:100]
    category = request.GET.get("category", "").strip()[:80]
    status = request.GET.get("status", "active")

    if status not in {"active", "archived", "all"}:
        status = "active"

    products = Product.objects.all()

    if status != "all":
        products = products.filter(is_active=status == "active")

    if query:
        products = products.filter(
            Q(name__icontains=query)
            | Q(sku__icontains=query)
            | Q(category__icontains=query)
        )

    if category:
        products = products.filter(category=category)

    context = workspace_context(request)
    context.update(
        {
            "active_page": "products",
            "page_obj": Paginator(products, 12).get_page(
                request.GET.get("page")
            ),
            "query": query,
            "selected_category": category,
            "selected_status": status,
            "categories": (
                Product.objects.exclude(category="")
                .order_by("category")
                .values_list("category", flat=True)
                .distinct()
            ),
            "pagination_query": pagination_query(request),
        }
    )
    return render_list(
        request,
        "inventory/product_list.html",
        "inventory/_product_rows.html",
        context,
    )


@never_cache
@login_required(login_url="login")
@permission_required(
    ["inventory.view_product", "inventory.add_product"],
    raise_exception=True,
)
@require_http_methods(["GET", "POST"])
def product_create(request):
    form = ProductForm(request.POST if request.method == "POST" else None)

    if request.method == "POST" and form.is_valid():
        product = save_product_form(form)

        if product is not None:
            record(
                request,
                AuditEntry.Action.CREATE,
                f"Created product {product.name}",
                obj=product,
            )
            messages.success(request, f"{product.name} was created.")
            return redirect("product-list")

    context = workspace_context(request)
    context.update(
        {
            "active_page": "products",
            "form": form,
            "page_title": "Add product",
            "submit_label": "Create product",
        }
    )
    return render(request, "inventory/product_form.html", context)


@never_cache
@login_required(login_url="login")
@permission_required(
    ["inventory.view_product", "inventory.change_product"],
    raise_exception=True,
)
@require_http_methods(["GET", "POST"])
def product_update(request, pk):
    product = get_object_or_404(Product, pk=pk)
    before = snapshot(product, PRODUCT_AUDIT_FIELDS)
    form = ProductForm(
        request.POST if request.method == "POST" else None,
        instance=product,
    )

    if request.method == "POST" and form.is_valid():
        saved_product = save_product_form(form)

        if saved_product is not None:
            record(
                request,
                AuditEntry.Action.UPDATE,
                f"Updated product {saved_product.name}",
                obj=saved_product,
                changes=diff(before, snapshot(saved_product, PRODUCT_AUDIT_FIELDS)),
            )
            messages.success(request, f"{saved_product.name} was updated.")
            return redirect("product-list")

    context = workspace_context(request)
    context.update(
        {
            "active_page": "products",
            "form": form,
            "page_title": "Edit product",
            "submit_label": "Save changes",
            "history": history_for(product),
        }
    )
    return render(request, "inventory/product_form.html", context)


@never_cache
@login_required(login_url="login")
@permission_required(
    ["inventory.view_product", "inventory.change_product"],
    raise_exception=True,
)
@require_http_methods(["GET", "POST"])
def product_status(request, pk):
    product = get_object_or_404(products_with_stock(), pk=pk)

    if request.method == "POST":
        action = request.POST.get("action")

        if action not in {"archive", "restore"}:
            return HttpResponseBadRequest("Invalid product action.")

        with transaction.atomic():
            locked_product = get_object_or_404(
                Product.objects.select_for_update(),
                pk=pk,
            )
            quantity = (
                StockBalance.objects.filter(product=locked_product)
                .values_list("quantity", flat=True)
                .first()
            ) or 0

            if action == "archive" and quantity > 0:
                messages.error(
                    request,
                    "Issue the remaining stock before archiving this product.",
                )
                return redirect("product-status", pk=pk)

            locked_product.is_active = action == "restore"
            locked_product.save(
                update_fields=["is_active", "updated_at"]
            )

        outcome = "restored" if action == "restore" else "archived"
        record(
            request,
            AuditEntry.Action.STATUS,
            f"{outcome.title()} product {product.name}",
            obj=locked_product,
        )
        messages.success(request, f"{product.name} was {outcome}.")
        return redirect("product-list")

    context = workspace_context(request)
    context.update(
        {
            "active_page": "products",
            "product": product,
        }
    )
    return render(request, "inventory/product_status.html", context)


@never_cache
@login_required(login_url="login")
@permission_required("inventory.view_product", raise_exception=True)
def stock_list(request):
    query = request.GET.get("q", "").strip()[:100]
    health = request.GET.get("health", "all")

    if health not in {"all", "healthy", "low", "out"}:
        health = "all"

    products = products_with_stock().filter(is_active=True)

    if query:
        products = products.filter(
            Q(name__icontains=query)
            | Q(sku__icontains=query)
            | Q(category__icontains=query)
        )

    if health == "healthy":
        products = products.filter(on_hand__gt=F("reorder_level"))
    elif health == "low":
        products = products.filter(
            on_hand__gt=0,
            on_hand__lte=F("reorder_level"),
        )
    elif health == "out":
        products = products.filter(on_hand=0)

    context = workspace_context(request)
    context.update(
        {
            "active_page": "stock",
            "page_obj": Paginator(products, 12).get_page(
                request.GET.get("page")
            ),
            "query": query,
            "selected_health": health,
            "stock_totals": active_stock_totals(),
            "pagination_query": pagination_query(request),
        }
    )
    return render_list(
        request,
        "inventory/stock_list.html",
        "inventory/_stock_rows.html",
        context,
    )


@never_cache
@login_required(login_url="login")
@permission_required("inventory.view_product", raise_exception=True)
@require_http_methods(["GET", "POST"])
def stock_create(request, pk, kind):
    actions = {
        StockMovement.Kind.RECEIVE: (
            "inventory.receive_stock",
            "Receive stock",
        ),
        StockMovement.Kind.ISSUE: (
            "inventory.issue_stock",
            "Issue stock",
        ),
    }

    if kind not in actions:
        raise Http404("Unknown stock operation.")

    permission, title = actions[kind]

    if not request.user.has_perm(permission):
        raise PermissionDenied("You cannot perform this stock operation.")

    product = get_object_or_404(products_with_stock(), pk=pk)
    form = StockMovementForm(
        request.POST if request.method == "POST" else None
    )

    if request.method == "POST" and form.is_valid():
        try:
            result = record_stock_movement(
                product_id=product.pk,
                actor=request.user,
                kind=kind,
                quantity=form.cleaned_data["quantity"],
                reason=form.cleaned_data["reason"],
                operation_id=form.cleaned_data["operation_id"],
            )
        except StockError as error:
            form.add_error(None, str(error))
            product = get_object_or_404(products_with_stock(), pk=pk)
        else:
            if result.created:
                verb = "Received" if kind == "receive" else "Issued"
                record(
                    request,
                    AuditEntry.Action.STOCK,
                    f"{verb} {result.movement.quantity} units of {product.name}",
                    obj=product,
                )
                messages.success(
                    request,
                    f"{verb} {result.movement.quantity} units for {product.name}.",
                )
            else:
                messages.info(
                    request,
                    "This operation was already recorded. Stock was not changed again.",
                )

            return redirect("stock-list")

    context = workspace_context(request)
    context.update(
        {
            "active_page": "stock",
            "product": product,
            "form": form,
            "kind": kind,
            "page_title": title,
        }
    )
    return render(request, "inventory/stock_form.html", context)


@never_cache
@login_required(login_url="login")
@permission_required(
    ["inventory.view_product", "inventory.view_stockmovement"],
    raise_exception=True,
)
def movement_list(request):
    form = MovementFilterForm(request.GET)
    movements = StockMovement.objects.select_related(
        "product",
        "actor",
        "purchase_order",
        "purchase_order__supplier",
        "reversal_of",
        "reversal",
        "sales_line",
    )

    if form.is_valid():
        query = form.cleaned_data["q"]
        kind = form.cleaned_data["kind"]

        if query:
            movements = movements.filter(
                Q(product__name__icontains=query)
                | Q(product__sku__icontains=query)
                | Q(reason__icontains=query)
                | Q(actor__username__icontains=query)
            )

        if kind:
            movements = movements.filter(kind=kind)
    else:
        movements = movements.none()

    context = workspace_context(request)
    context.update(
        {
            "active_page": "movements",
            "filter_form": form,
            "page_obj": Paginator(movements, 20).get_page(
                request.GET.get("page")
            ),
            "pagination_query": pagination_query(request),
        }
    )
    return render_list(
        request,
        "inventory/movement_list.html",
        "inventory/_movement_rows.html",
        context,
    )
"""Workspace and product catalog views."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import ProductForm
from .models import Product


def workspace_context(request):
    user = request.user

    if user.is_superuser:
        access_label = "Administrator"
    elif user.is_staff:
        access_label = "Administration access"
    else:
        access_label = "Member"

    return {
        "display_name": user.first_name or user.get_username(),
        "access_label": access_label,
    }


def save_product_form(form):
    """Save a valid form, handling a competing request for the same SKU."""
    try:
        with transaction.atomic():
            return form.save()
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
@login_required(login_url="login")
def overview(request):
    context = workspace_context(request)
    context["active_page"] = "overview"

    if request.user.has_perm("inventory.view_product"):
        context["catalog_totals"] = Product.objects.aggregate(
            total=Count("pk"),
            active=Count("pk", filter=Q(is_active=True)),
            archived=Count("pk", filter=Q(is_active=False)),
        )
        context["recent_products"] = Product.objects.order_by(
            "-updated_at", "-pk"
        )[:5]

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

    paginator = Paginator(products, 12)
    page = paginator.get_page(request.GET.get("page"))

    parameters = request.GET.copy()
    parameters.pop("page", None)

    context = workspace_context(request)
    context.update(
        {
            "active_page": "products",
            "page_obj": page,
            "query": query,
            "selected_category": category,
            "selected_status": status,
            "categories": (
                Product.objects.exclude(category="")
                .order_by("category")
                .values_list("category", flat=True)
                .distinct()
            ),
            "pagination_query": parameters.urlencode(),
        }
    )

    return render(request, "inventory/product_list.html", context)


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
    form = ProductForm(
        request.POST if request.method == "POST" else None,
        instance=product,
    )

    if request.method == "POST" and form.is_valid():
        saved_product = save_product_form(form)

        if saved_product is not None:
            messages.success(request, f"{saved_product.name} was updated.")
            return redirect("product-list")

    context = workspace_context(request)
    context.update(
        {
            "active_page": "products",
            "form": form,
            "page_title": "Edit product",
            "submit_label": "Save changes",
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
    product = get_object_or_404(Product, pk=pk)

    if request.method == "POST":
        action = request.POST.get("action")

        if action not in {"archive", "restore"}:
            return HttpResponseBadRequest("Invalid product action.")

        Product.objects.filter(pk=product.pk).update(
            is_active=action == "restore",
            updated_at=timezone.now(),
        )
        outcome = "restored" if action == "restore" else "archived"
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
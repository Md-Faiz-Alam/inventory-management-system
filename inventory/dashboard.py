"""Dashboard figures and chart data."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import (
    BigIntegerField,
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Q,
    Sum,
)
from django.db.models.functions import Coalesce, TruncDate
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .models import Product, PurchaseOrder, StockMovement

MONEY = DecimalField(max_digits=30, decimal_places=2)


def _stock():
    return Product.objects.filter(is_active=True).annotate(
        on_hand=Coalesce("stock_balance__quantity", 0, output_field=BigIntegerField())
    )


def stock_value():
    return _stock().aggregate(
        value=Coalesce(
            Sum(ExpressionWrapper(F("on_hand") * F("unit_price"), output_field=MONEY)),
            Decimal("0"),
            output_field=MONEY,
        )
    )["value"]


def low_stock_count():
    return _stock().filter(on_hand__lte=F("reorder_level")).count()


def dashboard_kpis(user):
    """Headline numbers, each shown only to users allowed to see the source."""
    data = {}
    if user.has_perm("inventory.view_product"):
        data["stock_value"] = stock_value()
    if user.has_perm("inventory.view_purchaseorder"):
        data["open_purchase_orders"] = (
            PurchaseOrder.objects.filter(cancelled=False)
            .exclude(received_quantity=F("quantity"))
            .count()
        )
    if user.has_perm("sales.view_salesorder"):
        from sales.models import SalesOrder, SalesOrderLine

        month_start = timezone.localdate().replace(day=1)
        data["sales_this_month"] = SalesOrderLine.objects.filter(
            order__status=SalesOrder.Status.FULFILLED,
            order__fulfilled_at__date__gte=month_start,
        ).aggregate(
            total=Coalesce(
                Sum(ExpressionWrapper(F("quantity") * F("unit_price"), output_field=MONEY)),
                Decimal("0"),
                output_field=MONEY,
            )
        )["total"]
        data["orders_to_fulfil"] = SalesOrder.objects.filter(
            status=SalesOrder.Status.CONFIRMED
        ).count()
    if user.has_perm("audit.view_auditentry"):
        from audit.models import AuditEntry

        data["recent_activity"] = AuditEntry.objects.exclude(
            action__in=["login", "logout", "login_failed"]
        )[:6]
    return data


def landing_figures():
    """Aggregate-only figures for the public page: no product names or prices."""
    from sales.models import SalesOrder

    stock = _stock()
    return {
        "products": stock.count(),
        "low": stock.filter(on_hand__lte=F("reorder_level")).count(),
        "open_pos": PurchaseOrder.objects.filter(cancelled=False)
        .exclude(received_quantity=F("quantity"))
        .count(),
        "orders": SalesOrder.objects.filter(status=SalesOrder.Status.FULFILLED).count(),
        "movements": StockMovement.objects.count(),
    }


def movement_series(days=30):
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    rows = (
        StockMovement.objects.filter(created_at__date__gte=start)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(
            received=Coalesce(Sum("quantity", filter=Q(kind="receive")), 0),
            issued=Coalesce(Sum("quantity", filter=Q(kind="issue")), 0),
        )
    )
    by_day = {row["day"]: row for row in rows}
    labels, received, issued = [], [], []
    for offset in range(days):
        day = start + timedelta(days=offset)
        labels.append(day.strftime("%d %b"))
        row = by_day.get(day, {})
        received.append(int(row.get("received", 0)))
        issued.append(int(row.get("issued", 0)))
    return {"labels": labels, "received": received, "issued": issued}


def value_by_category(limit=6):
    rows = list(
        _stock()
        .values("category")
        .annotate(
            value=Coalesce(
                Sum(ExpressionWrapper(F("on_hand") * F("unit_price"), output_field=MONEY)),
                Decimal("0"),
                output_field=MONEY,
            )
        )
        .order_by("-value")
    )
    head, tail = rows[:limit], rows[limit:]
    labels = [row["category"] or "Uncategorised" for row in head]
    values = [float(row["value"]) for row in head]
    if tail:
        labels.append("Other")
        values.append(float(sum(row["value"] for row in tail)))
    return {"labels": labels, "values": values}


def top_issued(days=30, limit=5):
    since = timezone.now() - timedelta(days=days)
    rows = (
        StockMovement.objects.filter(kind="issue", created_at__gte=since)
        .values("product__name")
        .annotate(units=Sum("quantity"))
        .order_by("-units")[:limit]
    )
    return {
        "labels": [row["product__name"] for row in rows],
        "values": [int(row["units"]) for row in rows],
    }


def health_split():
    counts = _stock().aggregate(
        healthy=Count("pk", filter=Q(on_hand__gt=F("reorder_level"))),
        low=Count("pk", filter=Q(on_hand__gt=0, on_hand__lte=F("reorder_level"))),
        out=Count("pk", filter=Q(on_hand=0)),
    )
    return {
        "labels": ["Healthy", "Low stock", "Out of stock"],
        "values": [counts["healthy"], counts["low"], counts["out"]],
    }


@never_cache
@login_required(login_url="login")
@require_GET
def dashboard_data(request):
    user = request.user
    payload = {}
    if user.has_perm("inventory.view_product"):
        payload["value_by_category"] = value_by_category()
        payload["health"] = health_split()
        if user.has_perm("inventory.view_stockmovement"):
            payload["movements"] = movement_series()
            payload["top_issued"] = top_issued()
    return JsonResponse(payload)


@never_cache
@login_required(login_url="login")
@require_GET
def alerts(request):
    """Small fragment polled by the top bar bell."""
    count = (
        low_stock_count() if request.user.has_perm("inventory.view_product") else 0
    )
    return render(request, "inventory/_alerts.html", {"alert_count": count})

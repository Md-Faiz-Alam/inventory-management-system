"""Read-only stock reports and bounded CSV exports."""

from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import BigIntegerField, Case, Count, F, Q, Sum, When
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from inventory.models import StockMovement
from inventory.views import products_with_stock, workspace_context

from config.pdf import render_pdf

from .csv_exports import csv_response
from .forms import MovementReportForm, StockReportForm
from .snapshots import snapshot_page

MAX_EXPORT_ROWS = 10_000
MAX_PDF_ROWS = 500
PAGE_SIZE = 25


def stock_row(record):
    quantity = record["on_hand"]

    if quantity == 0:
        health = "Out of stock"
    elif quantity <= record["reorder_level"]:
        health = "Low stock"
    else:
        health = "Healthy"

    return [
        record["sku"],
        record["name"],
        record["category"],
        quantity,
        record["reorder_level"],
        health,
        format(record["unit_price"], ".2f"),
    ]


def movement_row(record):
    received = record["kind"] == StockMovement.Kind.RECEIVE

    return [
        timezone.localtime(
            record["created_at"]
        ).isoformat(timespec="seconds"),
        record["product__sku"],
        record["product__name"],
        "Stock received" if received else "Stock issued",
        record["quantity"] if received else -record["quantity"],
        record["balance_before"],
        record["balance_after"],
        record["actor__username"],
        record["reason"],
        movement_document(record),
    ]


def movement_document(record):
    """Describe the business document behind a movement, if any."""
    parts = []
    if record.get("purchase_order__reference"):
        parts.append(f"PO {record['purchase_order__reference']}")
    if record.get("document_reference"):
        parts.append(record["document_reference"])
    if record.get("reversal_of_id"):
        parts.append(f"Reversal of #{record['reversal_of_id']}")
    if record.get("counted_quantity") is not None:
        parts.append(f"Stocktake count {record['counted_quantity']}")
    return " · ".join(parts)


def report_summary(records, summary_fields):
    aliases = {f"summary_{index}": label for index, label in enumerate(summary_fields)}
    totals = records.order_by().aggregate(
        **{alias: summary_fields[label] for alias, label in aliases.items()}
    )
    return {label: totals[alias] or 0 for alias, label in aliases.items()}


def report_response(
    request,
    *,
    form,
    records,
    columns,
    row_builder,
    title,
    description,
    filename,
    report_kind,
    summary_fields,
    order_by,
):
    output = request.GET.get("format", "html")

    if output not in {"html", "csv", "pdf"}:
        form.add_error(None, "Unsupported report format.")

    if not form.errors and output == "pdf":
        pdf_records = list(records[: MAX_PDF_ROWS + 1])
        return render_pdf(
            request,
            "pdf/report.html",
            {
                "title": title,
                "description": description,
                "columns": columns,
                "rows": [row_builder(record) for record in pdf_records[:MAX_PDF_ROWS]],
                "row_count": min(len(pdf_records), MAX_PDF_ROWS),
                "truncated": len(pdf_records) > MAX_PDF_ROWS,
                "summary": report_summary(records, summary_fields),
            },
            filename.replace(".csv", ".pdf"),
        )

    if not form.errors and output == "csv":
        export_records = list(
            records[: MAX_EXPORT_ROWS + 1]
        )

        if len(export_records) > MAX_EXPORT_ROWS:
            form.add_error(
                None,
                f"Export exceeds {MAX_EXPORT_ROWS:,} rows. "
                "Narrow the filters and try again. "
                "No file was exported.",
            )
        else:
            return csv_response(
                filename,
                columns,
                (
                    row_builder(record)
                    for record in export_records
                ),
            )

    valid = not form.errors

    if valid:
        page, summary = snapshot_page(
            records,
            summary_fields=summary_fields,
            order_by=order_by,
            page_number=request.GET.get("page"),
            page_size=PAGE_SIZE,
        )
    else:
        page = Paginator([], PAGE_SIZE).page(1)
        summary = {}

    parameters = request.GET.copy()
    parameters.pop("page", None)
    parameters.pop("format", None)

    export_parameters = parameters.copy()
    export_parameters["format"] = "csv"
    pdf_parameters = parameters.copy()
    pdf_parameters["format"] = "pdf"

    context = workspace_context(request)
    context.update(
        {
            "active_page": "reports",
            "report_kind": report_kind,
            "page_title": title,
            "description": description,
            "filter_form": form,
            "filters_valid": valid,
            "columns": columns,
            "rows": [
                row_builder(record)
                for record in page
            ],
            "page_obj": page,
            "summary": summary,
            "pagination_query": parameters.urlencode(),
            "export_query": export_parameters.urlencode(),
            "pdf_query": pdf_parameters.urlencode(),
            "max_pdf_rows": MAX_PDF_ROWS,
            "max_export_rows": MAX_EXPORT_ROWS,
            "report_timezone": str(
                timezone.get_current_timezone()
            ),
            "generated_at": timezone.now(),
        }
    )

    return render(
        request,
        "reports/report.html",
        context,
        status=200 if valid else 400,
    )


@never_cache
@login_required(login_url="login")
@permission_required(
    "inventory.view_product",
    raise_exception=True,
)
@require_http_methods(["GET"])
def stock_report(request):
    form = StockReportForm(request.GET)
    products = products_with_stock().filter(is_active=True)

    if form.is_valid():
        query = form.cleaned_data["q"]
        health = form.cleaned_data["health"]

        if query:
            products = products.filter(
                Q(name__icontains=query)
                | Q(sku__icontains=query)
                | Q(category__icontains=query)
            )

        if health == "healthy":
            products = products.filter(
                on_hand__gt=F("reorder_level")
            )
        elif health == "low":
            products = products.filter(
                on_hand__gt=0,
                on_hand__lte=F("reorder_level"),
            )
        elif health == "out":
            products = products.filter(on_hand=0)

    records = products.order_by("name", "pk").values(
        "sku",
        "name",
        "category",
        "on_hand",
        "reorder_level",
        "unit_price",
    )

    return report_response(
        request,
        form=form,
        records=records,
        columns=[
            "SKU",
            "Product",
            "Category",
            "Available",
            "Reorder level",
            "Stock level",
            "Unit price (INR)",
        ],
        row_builder=stock_row,
        title="Current stock report",
        description=(
            "Active products and current balances. "
            "Unit prices are current catalog prices, "
            "not historical purchase costs."
        ),
        filename="stock-report.csv",
        report_kind="stock",
        summary_fields={
            "Products": Count("pk"),
            "Units on hand": Sum("on_hand"),
            "Low stock": Count(
                "pk", filter=Q(on_hand__gt=0, on_hand__lte=F("reorder_level"))
            ),
            "Out of stock": Count("pk", filter=Q(on_hand=0)),
        },
        order_by=("name", "pk"),
    )


@never_cache
@login_required(login_url="login")
@permission_required(
    [
        "inventory.view_product",
        "inventory.view_stockmovement",
    ],
    raise_exception=True,
)
@require_http_methods(["GET"])
def movement_report(request):
    form = MovementReportForm(request.GET)
    movements = StockMovement.objects.all()

    if form.is_valid():
        query = form.cleaned_data["q"]
        kind = form.cleaned_data["kind"]
        start = form.cleaned_data["start"]
        end = form.cleaned_data["end"]

        if query:
            movements = movements.filter(
                Q(product__name__icontains=query)
                | Q(product__sku__icontains=query)
                | Q(reason__icontains=query)
                | Q(actor__username__icontains=query)
            )

        if kind:
            movements = movements.filter(kind=kind)

        if start:
            movements = movements.filter(
                created_at__date__gte=start
            )

        if end:
            movements = movements.filter(
                created_at__date__lte=end
            )

    records = movements.order_by(
        "-created_at",
        "-pk",
    ).values(
        "created_at",
        "product__sku",
        "product__name",
        "kind",
        "quantity",
        "balance_before",
        "balance_after",
        "actor__username",
        "reason",
        "purchase_order__reference",
        "document_reference",
        "reversal_of_id",
        "counted_quantity",
    )

    return report_response(
        request,
        form=form,
        records=records,
        columns=[
            "Recorded at (UTC offset included)",
            "SKU",
            "Product",
            "Movement",
            "Quantity change",
            "Balance before",
            "Balance after",
            "Recorded by",
            "Reason",
            "Document",
        ],
        row_builder=movement_row,
        title="Stock movement report",
        description=(
            "Recorded receipts and issues, including archived products. "
            "Date filters include both boundary dates "
            "in the displayed timezone."
        ),
        filename="movement-report.csv",
        report_kind="movements",
        summary_fields={
            "Movements": Count("pk"),
            "Units received": Sum("quantity", filter=Q(kind="receive")),
            "Units issued": Sum("quantity", filter=Q(kind="issue")),
            "Net units": Sum(
                Case(
                    When(kind="receive", then=F("quantity")),
                    default=-F("quantity"),
                    output_field=BigIntegerField(),
                )
            ),
        },
        order_by=("-created_at", "-pk"),
    )
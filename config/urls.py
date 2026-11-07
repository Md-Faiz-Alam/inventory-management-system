"""Application URL configuration."""

from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

from audit import views as audit_views
from inventory import dashboard, purchasing, views


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", healthz, name="healthz"),
    path("accounts/", include("accounts.urls")),
    path("api/", include("api.urls")),
    path("", views.overview, name="overview"),
    path("dashboard/data/", dashboard.dashboard_data, name="dashboard-data"),
    path("alerts/", dashboard.alerts, name="alerts"),
    path("products/", views.product_list, name="product-list"),
    path(
        "products/new/",
        views.product_create,
        name="product-create",
    ),
    path(
        "products/<int:pk>/edit/",
        views.product_update,
        name="product-update",
    ),
    path(
        "products/<int:pk>/status/",
        views.product_status,
        name="product-status",
    ),
    path("stock/", views.stock_list, name="stock-list"),
    path(
        "products/<int:pk>/receive/",
        views.stock_create,
        {"kind": "receive"},
        name="stock-receive",
    ),
    path(
        "products/<int:pk>/issue/",
        views.stock_create,
        {"kind": "issue"},
        name="stock-issue",
    ),
    path(
        "products/<int:pk>/stocktake/",
        purchasing.stocktake,
        name="stocktake",
    ),
    path(
        "movements/",
        views.movement_list,
        name="movement-list",
    ),
    path(
        "movements/<int:pk>/reverse/",
        purchasing.movement_reverse,
        name="movement-reverse",
    ),
    path("purchase-orders/", purchasing.po_list, name="po-list"),
    path("purchase-orders/new/", purchasing.po_create, name="po-create"),
    path("purchase-orders/<int:pk>/", purchasing.po_detail, name="po-detail"),
    path(
        "purchase-orders/<int:pk>/receive/",
        purchasing.po_receive,
        name="po-receive",
    ),
    path("purchase-orders/<int:pk>/pdf/", purchasing.po_pdf, name="po-pdf"),
    path(
        "purchase-orders/<int:pk>/cancel/",
        purchasing.po_cancel,
        name="po-cancel",
    ),
    path("activity/", audit_views.activity_list, name="activity"),
    path("suppliers/", include("suppliers.urls")),
    path("reports/", include("reports.urls")),
    path("", include("sales.urls")),
]

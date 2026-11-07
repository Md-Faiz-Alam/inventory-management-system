from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("products", views.ProductViewSet, basename="product")
router.register("suppliers", views.SupplierViewSet, basename="supplier")
router.register("customers", views.CustomerViewSet, basename="customer")
router.register("stock-movements", views.StockMovementViewSet, basename="stockmovement")
router.register("purchase-orders", views.PurchaseOrderViewSet, basename="purchaseorder")
router.register("sales-orders", views.SalesOrderViewSet, basename="salesorder")

urlpatterns = [
    path("v1/", include(router.urls)),
    path("v1/summary/", views.summary, name="api-summary"),
    path("v1/auth/token/", views.LockoutAwareTokenView.as_view(), name="api-token"),
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="api-schema"), name="api-docs"),
]

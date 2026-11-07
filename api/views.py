"""JSON API. Every stock change still goes through the inventory service."""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db.models import BigIntegerField
from django.db.models.functions import Coalesce
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts import lockout
from audit.log import record
from audit.models import AuditEntry
from inventory.dashboard import dashboard_kpis, health_split, value_by_category
from inventory.models import Product, PurchaseOrder, StockMovement
from inventory.services import StockError, record_stock_movement
from inventory.views import active_stock_totals
from sales.models import Customer, SalesOrder
from sales.services import SalesError, fulfil_order
from suppliers.models import Supplier

from .permissions import ModelPermissions
from .serializers import (
    CustomerSerializer,
    ProductSerializer,
    PurchaseOrderSerializer,
    SalesOrderSerializer,
    StockMovementCreateSerializer,
    StockMovementSerializer,
    SupplierSerializer,
)


class NoDeleteModelViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """Records are archived, never deleted, so the API offers no DELETE."""

    permission_classes = [IsAuthenticated, ModelPermissions]


class ProductViewSet(NoDeleteModelViewSet):
    serializer_class = ProductSerializer
    search_fields = ["name", "sku", "category"]

    def get_queryset(self):
        queryset = Product.objects.annotate(
            on_hand=Coalesce("stock_balance__quantity", 0, output_field=BigIntegerField())
        ).order_by("name", "pk")
        active = self.request.query_params.get("active")
        if active in {"true", "false"}:
            queryset = queryset.filter(is_active=active == "true")
        return queryset

    def perform_create(self, serializer):
        product = serializer.save()
        record(self.request, AuditEntry.Action.CREATE, f"Created product {product.name} via API", obj=product)

    def perform_update(self, serializer):
        product = serializer.save()
        record(self.request, AuditEntry.Action.UPDATE, f"Updated product {product.name} via API", obj=product)


class SupplierViewSet(NoDeleteModelViewSet):
    queryset = Supplier.objects.order_by("name", "pk")
    serializer_class = SupplierSerializer
    search_fields = ["name", "code", "contact_name", "email"]

    def _check_product_links(self, serializer):
        # Same rule as the web form: linking products needs catalog access.
        if "products" in serializer.validated_data and not self.request.user.has_perm(
            "inventory.view_product"
        ):
            raise PermissionDenied("Linking products requires catalog access.")

    def perform_create(self, serializer):
        self._check_product_links(serializer)
        supplier = serializer.save()
        record(self.request, AuditEntry.Action.CREATE, f"Created supplier {supplier.name} via API", obj=supplier)

    def perform_update(self, serializer):
        self._check_product_links(serializer)
        supplier = serializer.save()
        record(self.request, AuditEntry.Action.UPDATE, f"Updated supplier {supplier.name} via API", obj=supplier)


class CustomerViewSet(NoDeleteModelViewSet):
    queryset = Customer.objects.order_by("name", "pk")
    serializer_class = CustomerSerializer
    search_fields = ["name", "code", "email", "phone"]


class StockMovementViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = StockMovement.objects.select_related("product", "actor")
    serializer_class = StockMovementSerializer
    permission_classes = [IsAuthenticated, ModelPermissions]
    search_fields = ["product__name", "product__sku", "reason", "document_reference"]

    def get_permissions(self):
        if self.action == "create":
            # The stock service checks receive/issue permissions itself.
            return [IsAuthenticated()]
        return super().get_permissions()

    @extend_schema(request=StockMovementCreateSerializer, responses=StockMovementSerializer)
    def create(self, request):
        payload = StockMovementCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        try:
            result = record_stock_movement(
                product_id=data["product"].pk,
                actor=request.user,
                kind=data["kind"],
                quantity=data["quantity"],
                reason=data["reason"],
                operation_id=data["operation_id"],
            )
        except DjangoPermissionDenied as error:
            raise PermissionDenied(str(error)) from error
        except StockError as error:
            raise ValidationError({"detail": str(error)}) from error
        if result.created:
            record(
                request,
                AuditEntry.Action.STOCK,
                f"{result.movement.get_kind_display()}: {result.movement.quantity} "
                f"of {data['product'].name} via API",
                obj=data["product"],
            )
        return Response(
            StockMovementSerializer(result.movement).data,
            status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
        )


class PurchaseOrderViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PurchaseOrder.objects.order_by("-created_at", "-pk")
    serializer_class = PurchaseOrderSerializer
    permission_classes = [IsAuthenticated, ModelPermissions]
    search_fields = ["reference"]


class SalesOrderViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SalesOrder.objects.prefetch_related("lines__product").order_by(
        "-created_at", "-pk"
    )
    serializer_class = SalesOrderSerializer
    permission_classes = [IsAuthenticated, ModelPermissions]
    search_fields = ["number", "customer__name"]

    @extend_schema(request=None, responses=SalesOrderSerializer)
    @action(detail=True, methods=["post"])
    def fulfil(self, request, pk=None):
        """Issue stock for every line of a confirmed order, atomically."""
        self.get_object()
        try:
            order = fulfil_order(order_id=int(pk), actor=request.user)
        except DjangoPermissionDenied as error:
            raise PermissionDenied(str(error)) from error
        except SalesError as error:
            raise ValidationError({"detail": str(error)}) from error
        record(request, AuditEntry.Action.ORDER, f"Sales order {order.number} fulfilled via API", obj=order)
        return Response(SalesOrderSerializer(self.get_queryset().get(pk=order.pk)).data)


class LockoutAwareTokenView(ObtainAuthToken):
    """Token login with the same failed-attempt lockout as the sign-in page."""

    def post(self, request, *args, **kwargs):
        username = str(request.data.get("username", ""))[:150]
        if lockout.is_locked(request, username):
            return Response(
                {"detail": "Too many failed attempts. Try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            lockout.record_failure(request, username)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        lockout.clear(request, username)
        user = serializer.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key})


@extend_schema(responses=dict)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def summary(request):
    """Headline figures for dashboards and integrations."""
    if not request.user.has_perm("inventory.view_product"):
        raise PermissionDenied("Catalog access is required.")
    kpis = dashboard_kpis(request.user)
    kpis.pop("recent_activity", None)
    return Response(
        {
            "stock": active_stock_totals(),
            "kpis": {key: str(value) for key, value in kpis.items()},
            "health": health_split(),
            "value_by_category": value_by_category(),
        }
    )

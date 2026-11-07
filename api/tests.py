"""REST API permissions and stock operations."""

from uuid import uuid4

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.roles import MANAGER, STAFF
from config.testing import PASSWORD, make_product, make_user, receive
from inventory.models import StockBalance


class ApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = make_user("api-manager", MANAGER)
        cls.staff = make_user("api-staff", STAFF)
        cls.outsider = make_user("api-outsider")
        cls.product = make_product("API-1", "API Product")
        receive(cls.product, cls.manager, 8)

    def setUp(self):
        self.api = APIClient()

    def test_anonymous_requests_are_rejected(self):
        self.assertIn(self.api.get("/api/v1/products/").status_code, {401, 403})

    def test_reading_requires_view_permission(self):
        self.api.force_authenticate(self.outsider)
        self.assertEqual(self.api.get("/api/v1/products/").status_code, 403)

    def test_product_list_includes_stock(self):
        self.api.force_authenticate(self.staff)
        response = self.api.get("/api/v1/products/", {"search": "api"})
        self.assertEqual(response.status_code, 200)
        row = response.json()["results"][0]
        self.assertEqual((row["sku"], row["on_hand"], row["stock_status"]), ("API-1", 8, "healthy"))

    def test_staff_cannot_create_products_manager_can(self):
        payload = {"name": "New", "sku": "new-1", "unit_price": "10.00", "reorder_level": 2}
        self.api.force_authenticate(self.staff)
        self.assertEqual(self.api.post("/api/v1/products/", payload).status_code, 403)
        self.api.force_authenticate(self.manager)
        response = self.api.post("/api/v1/products/", payload)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["sku"], "NEW-1")

    def test_delete_is_not_offered(self):
        self.api.force_authenticate(make_user("api-root", is_superuser=True))
        response = self.api.delete(f"/api/v1/products/{self.product.pk}/")
        self.assertEqual(response.status_code, 405)

    def test_stock_movement_via_api_is_idempotent(self):
        self.api.force_authenticate(self.staff)
        payload = {
            "product": self.product.pk,
            "kind": "issue",
            "quantity": 3,
            "reason": "API issue",
            "operation_id": str(uuid4()),
        }
        first = self.api.post("/api/v1/stock-movements/", payload, format="json")
        second = self.api.post("/api/v1/stock-movements/", payload, format="json")
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(StockBalance.objects.get(product=self.product).quantity, 5)

        payload.update(operation_id=str(uuid4()), quantity=99)
        response = self.api.post("/api/v1/stock-movements/", payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("exceeds", response.json()["detail"])

    def test_token_authentication(self):
        token = Token.objects.create(user=self.manager)
        self.api.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.api.get("/api/v1/summary/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stock"]["units"], 8)

    def test_schema_and_docs_are_public(self):
        self.assertEqual(self.client.get(reverse("api-schema")).status_code, 200)
        self.assertEqual(self.client.get(reverse("api-docs")).status_code, 200)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class ApiHardeningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.manager = make_user("hard-manager", MANAGER)
        self.api = APIClient()

    def test_token_endpoint_locks_after_repeated_failures(self):
        for _ in range(5):
            self.api.post("/api/v1/auth/token/", {"username": "hard-manager", "password": "x"})
        response = self.api.post(
            "/api/v1/auth/token/", {"username": "hard-manager", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 429)

    def test_token_endpoint_returns_token(self):
        response = self.api.post(
            "/api/v1/auth/token/", {"username": "hard-manager", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.json())

    def test_duplicate_codes_return_400_not_500(self):
        self.api.force_authenticate(self.manager)
        first = self.api.post("/api/v1/customers/", {"name": "A", "code": "c-1"})
        second = self.api.post("/api/v1/customers/", {"name": "B", "code": "C-1"})
        self.assertEqual((first.status_code, second.status_code), (201, 400))
        self.api.post("/api/v1/suppliers/", {"name": "S", "code": "s-1"})
        response = self.api.post("/api/v1/suppliers/", {"name": "T", "code": "S-1"})
        self.assertEqual(response.status_code, 400)

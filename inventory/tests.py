"""Tests for authentication, catalog, stock engine, and stock pages."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import PermissionDenied
from django.db import (
    DatabaseError,
    IntegrityError,
    close_old_connections,
    connection,
    transaction,
)
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from config.editing import edit_token

from .forms import ProductForm
from .models import (
    MAX_STOCK_QUANTITY,
    Product,
    StockBalance,
    StockMovement,
)
from .services import StockError, record_stock_movement
from .views import save_product_form


class WorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="warehouse-user",
            email="warehouse@example.com",
            password="Workspace-test-password-482!",
        )

    def test_anonymous_visitor_sees_public_landing_page(self):
        response = self.client.get(reverse("overview"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "landing.html")
        self.assertContains(response, "Try the live demo")
        self.assertNotContains(response, "Sign out")

    def test_anonymous_visitor_is_redirected_to_login(self):
        response = self.client.get(reverse("product-list"))
        self.assertRedirects(response, f"{reverse('login')}?next=/products/")

    def test_login_page_renders(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome back.")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_authenticated_user_sees_account_details(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "warehouse@example.com")
        self.assertContains(response, "Your inventory starts here.")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_member_does_not_see_administration_link(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("overview"))
        self.assertNotContains(response, f'href="{reverse("admin:index")}"')

    def test_staff_user_sees_administration_link(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        response = self.client.get(reverse("overview"))
        self.assertContains(response, f'href="{reverse("admin:index")}"')

    def test_valid_login_redirects_to_workspace(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.user.username,
                "password": "Workspace-test-password-482!",
            },
        )
        self.assertRedirects(response, reverse("overview"))

    def test_login_rejects_external_redirect(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.user.username,
                "password": "Workspace-test-password-482!",
                "next": "https://example.com/untrusted",
            },
        )
        self.assertRedirects(response, reverse("overview"))

    def test_invalid_password_does_not_authenticate(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.user.username,
                "password": "incorrect-password",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign-in failed.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_get_request_cannot_log_user_out(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("logout"))
        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_logout_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(reverse("logout"))
        self.assertEqual(response.status_code, 403)
        self.assertIn("_auth_user_id", client.session)

    def test_logout_with_csrf_token_ends_session(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        client.get(reverse("overview"))
        response = client.post(
            reverse("logout"),
            {"csrfmiddlewaretoken": client.cookies["csrftoken"].value},
        )
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", client.session)


class ProductCatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_superuser(
            username="catalog-admin",
            email="admin@example.com",
            password="Catalog-test-password-482!",
        )
        cls.viewer = user_model.objects.create_user(username="viewer")
        cls.viewer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory",
                codename="view_product",
            )
        )
        cls.member = user_model.objects.create_user(username="member")
        cls.product = Product.objects.create(
            name="USB-C Cable",
            sku="USB-C-001",
            category="Accessories",
            unit_price=Decimal("299.00"),
            reorder_level=5,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def product_data(self, **overrides):
        data = {
            "name": "Wireless Mouse",
            "sku": "MOUSE-001",
            "category": "Accessories",
            "description": "A compact wireless mouse.",
            "unit_price": "799.00",
            "reorder_level": "4",
            "edit_token": edit_token(self.product),
        }
        data.update(overrides)
        return data

    def test_anonymous_catalog_access_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("product-list"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('product-list')}",
        )

    def test_member_without_permission_cannot_view_catalog(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("product-list"))
        self.assertEqual(response.status_code, 403)

    def test_staff_flag_does_not_grant_catalog_permission(self):
        self.member.is_staff = True
        self.member.save(update_fields=["is_staff"])
        self.client.force_login(self.member)
        response = self.client.get(reverse("product-list"))
        self.assertEqual(response.status_code, 403)

    def test_viewer_can_read_but_cannot_write(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("product-list"))
        self.assertContains(response, "USB-C Cable")

        requests = [
            ("product-create", [], self.product_data()),
            ("product-update", [self.product.pk], self.product_data()),
            ("product-status", [self.product.pk], {"action": "archive"}),
        ]

        for name, arguments, data in requests:
            with self.subTest(endpoint=name):
                response = self.client.post(reverse(name, args=arguments), data)
                self.assertEqual(response.status_code, 403)

        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)
        self.assertEqual(self.product.name, "USB-C Cable")

    def test_create_product_normalizes_sku(self):
        response = self.client.post(
            reverse("product-create"),
            self.product_data(sku=" mouse-001 "),
        )
        self.assertRedirects(response, reverse("product-list"))
        product = Product.objects.get(sku="MOUSE-001")
        self.assertEqual(product.unit_price, Decimal("799.00"))

    def test_duplicate_sku_is_rejected_case_insensitively(self):
        form = ProductForm(self.product_data(sku="usb-c-001"))
        self.assertFalse(form.is_valid())
        self.assertIn("sku", form.errors)

    def test_archived_product_keeps_its_sku(self):
        Product.objects.filter(pk=self.product.pk).update(is_active=False)
        form = ProductForm(self.product_data(sku="USB-C-001"))
        self.assertFalse(form.is_valid())
        self.assertIn("sku", form.errors)

    def test_invalid_product_fields_are_rejected(self):
        for override in [
            {"unit_price": "-0.01"},
            {"reorder_level": "-1"},
            {"sku": "BAD SKU"},
            {"name": "   "},
        ]:
            with self.subTest(override=override):
                self.assertFalse(
                    ProductForm(self.product_data(**override)).is_valid()
                )

    def test_database_rejects_negative_price(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Product.objects.filter(pk=self.product.pk).update(
                    unit_price=Decimal("-1.00")
                )

    def test_database_rejects_case_insensitive_duplicate_sku(self):
        other = Product.objects.create(name="Other", sku="OTHER-001")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Product.objects.filter(pk=other.pk).update(sku="usb-c-001")

    def test_update_product(self):
        response = self.client.post(
            reverse("product-update", args=[self.product.pk]),
            self.product_data(name="Updated Cable", sku=self.product.sku),
        )
        self.assertRedirects(response, reverse("product-list"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Updated Cable")
        self.assertEqual(self.product.sku, "USB-C-001")

    def test_archive_confirmation_does_not_modify_product(self):
        response = self.client.get(
            reverse("product-status", args=[self.product.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)

    def test_archive_and_restore_preserve_product(self):
        url = reverse("product-status", args=[self.product.pk])
        response = self.client.post(url, {"action": "archive"})
        self.assertRedirects(response, reverse("product-list"))
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)

        response = self.client.post(url, {"action": "restore"})
        self.assertRedirects(response, reverse("product-list"))
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)
        self.assertEqual(Product.objects.count(), 1)

    def test_invalid_status_action_is_rejected(self):
        response = self.client.post(
            reverse("product-status", args=[self.product.pk]),
            {"action": "delete"},
        )
        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)

    def test_search_and_category_filter(self):
        Product.objects.create(
            name="Desk Lamp", sku="LAMP-001", category="Lighting"
        )
        response = self.client.get(
            reverse("product-list"),
            {"q": "USB", "category": "Accessories"},
        )
        self.assertContains(response, "USB-C Cable")
        self.assertNotContains(response, "Desk Lamp")

    def test_archived_products_are_hidden_by_default(self):
        Product.objects.filter(pk=self.product.pk).update(is_active=False)
        response = self.client.get(reverse("product-list"))
        self.assertNotContains(response, "USB-C Cable")
        response = self.client.get(
            reverse("product-list"), {"status": "archived"}
        )
        self.assertContains(response, "USB-C Cable")

    def test_pagination_and_invalid_page(self):
        for number in range(13):
            Product.objects.create(
                name=f"Item {number:02d}", sku=f"ITEM-{number:02d}"
            )
        response = self.client.get(reverse("product-list"), {"page": "1"})
        self.assertEqual(len(response.context["page_obj"]), 12)
        response = self.client.get(reverse("product-list"), {"page": "2"})
        self.assertEqual(len(response.context["page_obj"]), 2)
        response = self.client.get(
            reverse("product-list"), {"page": "invalid"}
        )
        self.assertEqual(response.context["page_obj"].number, 1)

    def test_overview_uses_actual_catalog_totals(self):
        Product.objects.create(
            name="Old Keyboard", sku="OLD-001", is_active=False
        )
        response = self.client.get(reverse("overview"))
        self.assertEqual(
            response.context["catalog_totals"],
            {"total": 2, "active": 1, "archived": 1},
        )

    def test_overview_does_not_expose_catalog_to_unpermitted_member(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("overview"))
        self.assertNotIn("catalog_totals", response.context)
        self.assertNotContains(response, "USB-C Cable")

    def test_create_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post(
            reverse("product-create"), self.product_data()
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Product.objects.filter(sku="MOUSE-001").exists())

    def test_missing_product_returns_404(self):
        response = self.client.get(
            reverse("product-update", args=[999999])
        )
        self.assertEqual(response.status_code, 404)


class StockEngineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = get_user_model().objects.create_superuser(
            username="stock-admin",
            email="stock@example.com",
            password="Stock-test-password-482!",
        )
        cls.product = Product.objects.create(
            name="Stock Test Product", sku="STOCK-001"
        )

    def move(self, **overrides):
        data = {
            "product_id": self.product.pk,
            "actor": self.actor,
            "kind": StockMovement.Kind.RECEIVE,
            "quantity": 10,
            "reason": "Delivery received",
            "operation_id": uuid4(),
        }
        data.update(overrides)
        return record_stock_movement(**data)

    def test_receive_records_balance_actor_and_reason(self):
        result = self.move(reason="  Delivery received  ")
        self.assertTrue(result.created)
        self.assertEqual(result.movement.actor_id, self.actor.pk)
        self.assertEqual(result.movement.reason, "Delivery received")
        self.assertEqual(result.movement.balance_before, 0)
        self.assertEqual(result.movement.balance_after, 10)
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 10
        )

    def test_issue_reduces_balance(self):
        self.move()
        result = self.move(
            kind=StockMovement.Kind.ISSUE,
            quantity=4,
            reason="Customer order",
        )
        self.assertEqual(result.movement.balance_before, 10)
        self.assertEqual(result.movement.balance_after, 6)
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 6
        )

    def test_insufficient_stock_changes_nothing(self):
        self.move(quantity=3)
        with self.assertRaises(StockError):
            self.move(kind=StockMovement.Kind.ISSUE, quantity=4)
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 3
        )

    def test_invalid_requests_create_no_stock_records(self):
        cases = [
            {"quantity": 0},
            {"quantity": -1},
            {"quantity": True},
            {"quantity": 1.5},
            {"quantity": "2"},
            {"quantity": MAX_STOCK_QUANTITY + 1},
            {"reason": ""},
            {"reason": "   "},
            {"reason": "x" * 501},
            {"kind": "unknown"},
            {"operation_id": "invalid"},
        ]
        for override in cases:
            with self.subTest(override=override):
                with self.assertRaises(StockError):
                    self.move(**override)
        self.assertFalse(StockBalance.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_archived_product_rejects_new_movements(self):
        Product.objects.filter(pk=self.product.pk).update(is_active=False)
        with self.assertRaises(StockError):
            self.move()
        self.assertFalse(StockBalance.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_anonymous_actor_is_rejected(self):
        with self.assertRaises(PermissionDenied):
            self.move(actor=AnonymousUser())
        self.assertFalse(StockMovement.objects.exists())

    def test_inactive_actor_is_rejected(self):
        self.actor.is_active = False
        try:
            with self.assertRaises(PermissionDenied):
                self.move()
        finally:
            self.actor.is_active = True
        self.assertFalse(StockMovement.objects.exists())

    def test_receive_permission_does_not_grant_issue_permission(self):
        receiver = get_user_model().objects.create_user(username="receiver")
        receiver.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="inventory",
                codename__in=["view_product", "receive_stock"],
            )
        )
        self.move(actor=receiver, quantity=3)
        with self.assertRaises(PermissionDenied):
            self.move(
                actor=receiver, kind=StockMovement.Kind.ISSUE, quantity=1
            )
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 3
        )

    def test_duplicate_request_changes_stock_once(self):
        operation_id = uuid4()
        first = self.move(operation_id=operation_id)
        second = self.move(operation_id=operation_id)
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.movement.pk, second.movement.pk)
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 10
        )

    def test_operation_id_cannot_be_reused_for_different_request(self):
        operation_id = uuid4()
        other = Product.objects.create(
            name="Other Stock Product", sku="STOCK-002"
        )
        self.move(operation_id=operation_id)
        cases = [
            {"quantity": 9},
            {"reason": "Different reason"},
            {"kind": StockMovement.Kind.ISSUE},
            {"product_id": other.pk},
        ]
        for override in cases:
            with self.subTest(override=override):
                with self.assertRaises(StockError):
                    self.move(operation_id=operation_id, **override)
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertFalse(
            StockBalance.objects.filter(product=other).exists()
        )

    def test_retry_after_archival_returns_original_result(self):
        operation_id = uuid4()
        first = self.move(operation_id=operation_id)
        Product.objects.filter(pk=self.product.pk).update(is_active=False)
        repeated = self.move(operation_id=operation_id)
        self.assertFalse(repeated.created)
        self.assertEqual(repeated.movement.pk, first.movement.pk)
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_receipt_cannot_overflow_stock_limit(self):
        self.move(quantity=MAX_STOCK_QUANTITY)
        with self.assertRaises(StockError):
            self.move(quantity=1)
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity,
            MAX_STOCK_QUANTITY,
        )
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_movement_failure_rolls_back_balance_change(self):
        self.move(quantity=5)
        with patch(
            "inventory.services.StockMovement.objects.create",
            side_effect=RuntimeError("Simulated write failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.move(quantity=3)
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 5
        )
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_database_rejects_negative_balance(self):
        self.move()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StockBalance.objects.filter(product=self.product).update(
                    quantity=-1
                )

    def test_database_rejects_inconsistent_movement(self):
        result = self.move()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StockMovement.objects.filter(
                    pk=result.movement.pk
                ).update(balance_after=99)

    def test_product_edit_preserves_stock_balance(self):
        self.move(quantity=12)
        self.client.force_login(self.actor)
        response = self.client.post(
            reverse("product-update", args=[self.product.pk]),
            {
                "name": "Renamed Product",
                "sku": self.product.sku,
                "category": "Updated category",
                "description": "",
                "unit_price": "10.00",
                "reorder_level": "5",
                "edit_token": edit_token(self.product),
            },
        )
        self.assertRedirects(response, reverse("product-list"))
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 12
        )

    def test_movement_history_has_consistent_running_balances(self):
        self.move(quantity=10)
        self.move(
            kind=StockMovement.Kind.ISSUE,
            quantity=4,
            reason="Order dispatched",
        )
        self.move(quantity=2, reason="Additional delivery")
        self.assertEqual(
            list(
                StockMovement.objects.order_by("pk").values_list(
                    "balance_before", "balance_after"
                )
            ),
            [(0, 10), (10, 6), (6, 8)],
        )
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 8
        )


class StockConcurrencyTests(TransactionTestCase):
    """Use independent PostgreSQL connections for competing requests."""

    def setUp(self):
        self.assertEqual(
            connection.vendor,
            "postgresql",
            "Concurrency tests must run against PostgreSQL.",
        )
        self.actor = get_user_model().objects.create_superuser(
            username="concurrency-admin",
            email="concurrency@example.com",
            password="Concurrency-test-password-482!",
        )
        self.product = Product.objects.create(
            name="Concurrent Product", sku="CONCURRENT-001"
        )

    def run_competing_requests(self, requests):
        barrier = Barrier(len(requests))
        actor_id = self.actor.pk
        product_id = self.product.pk

        def worker(payload):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=actor_id)
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '10s'")
                barrier.wait(timeout=10)
                try:
                    result = record_stock_movement(
                        product_id=product_id,
                        actor=actor,
                        reason="Concurrent operation",
                        **payload,
                    )
                    return ("ok", result.movement.pk, result.created)
                except StockError:
                    return ("rejected", None, False)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=len(requests)) as executor:
            futures = [
                executor.submit(worker, payload) for payload in requests
            ]
            return [future.result(timeout=20) for future in futures]

    def test_competing_issues_cannot_oversell(self):
        record_stock_movement(
            product_id=self.product.pk,
            actor=self.actor,
            kind=StockMovement.Kind.RECEIVE,
            quantity=10,
            reason="Opening receipt",
            operation_id=uuid4(),
        )
        results = self.run_competing_requests(
            [
                {
                    "kind": StockMovement.Kind.ISSUE,
                    "quantity": 7,
                    "operation_id": uuid4(),
                },
                {
                    "kind": StockMovement.Kind.ISSUE,
                    "quantity": 7,
                    "operation_id": uuid4(),
                },
            ]
        )
        self.assertCountEqual(
            [result[0] for result in results], ["ok", "rejected"]
        )
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 3
        )
        self.assertEqual(
            StockMovement.objects.filter(
                kind=StockMovement.Kind.ISSUE
            ).count(),
            1,
        )

    def test_competing_receipts_do_not_lose_updates(self):
        results = self.run_competing_requests(
            [
                {
                    "kind": StockMovement.Kind.RECEIVE,
                    "quantity": 4,
                    "operation_id": uuid4(),
                },
                {
                    "kind": StockMovement.Kind.RECEIVE,
                    "quantity": 6,
                    "operation_id": uuid4(),
                },
            ]
        )
        self.assertEqual([result[0] for result in results], ["ok", "ok"])
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 10
        )
        self.assertEqual(StockMovement.objects.count(), 2)

    def test_competing_duplicate_requests_change_stock_once(self):
        operation_id = uuid4()
        payload = {
            "kind": StockMovement.Kind.RECEIVE,
            "quantity": 8,
            "operation_id": operation_id,
        }
        results = self.run_competing_requests([payload, payload])
        self.assertEqual([result[0] for result in results], ["ok", "ok"])
        self.assertEqual(results[0][1], results[1][1])
        self.assertCountEqual(
            [result[2] for result in results], [True, False]
        )
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 8
        )

    def test_product_lock_is_held_until_transaction_finishes(self):
        product_id = self.product.pk

        def competing_lock():
            close_old_connections()
            try:
                try:
                    with transaction.atomic():
                        Product.objects.select_for_update(
                            nowait=True
                        ).get(pk=product_id)
                    return "acquired"
                except DatabaseError as error:
                    return getattr(error.__cause__, "sqlstate", None)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                record_stock_movement(
                    product_id=product_id,
                    actor=self.actor,
                    kind=StockMovement.Kind.RECEIVE,
                    quantity=1,
                    reason="Lock verification",
                    operation_id=uuid4(),
                )
                result = executor.submit(competing_lock).result(timeout=10)
                self.assertEqual(result, "55P03")


class StockWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = get_user_model().objects.create_superuser(
            username="web-stock-admin",
            email="web-stock@example.com",
            password="Web-stock-password-482!",
        )
        cls.viewer = get_user_model().objects.create_user(
            username="stock-viewer"
        )
        cls.viewer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory",
                codename="view_product",
            )
        )
        cls.product = Product.objects.create(
            name="Web Stock Product",
            sku="WEB-STOCK-001",
            reorder_level=5,
        )

    def setUp(self):
        self.client.force_login(self.actor)

    def payload(self, **overrides):
        data = {
            "operation_id": str(uuid4()),
            "quantity": "10",
            "reason": "Supplier delivery",
        }
        data.update(overrides)
        return data

    def receive(self, product=None, quantity=10):
        return record_stock_movement(
            product_id=(product or self.product).pk,
            actor=self.actor,
            kind=StockMovement.Kind.RECEIVE,
            quantity=quantity,
            reason="Test receipt",
            operation_id=uuid4(),
        )

    def test_stock_pages_require_login(self):
        self.client.logout()
        for name, arguments in [
            ("stock-list", []),
            ("movement-list", []),
            ("stock-receive", [self.product.pk]),
            ("stock-issue", [self.product.pk]),
        ]:
            with self.subTest(page=name):
                url = reverse(name, args=arguments)
                response = self.client.get(url)
                self.assertRedirects(
                    response, f"{reverse('login')}?next={url}"
                )

    def test_opening_stock_form_does_not_create_stock(self):
        response = self.client.get(
            reverse("stock-receive", args=[self.product.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Receive stock")
        UUID(str(response.context["form"]["operation_id"].value()))
        self.assertFalse(StockBalance.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_receive_and_issue_through_forms(self):
        response = self.client.post(
            reverse("stock-receive", args=[self.product.pk]),
            self.payload(),
        )
        self.assertRedirects(response, reverse("stock-list"))

        response = self.client.post(
            reverse("stock-issue", args=[self.product.pk]),
            self.payload(quantity="3", reason="Customer order"),
        )
        self.assertRedirects(response, reverse("stock-list"))

        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity,
            7,
        )
        self.assertEqual(StockMovement.objects.count(), 2)

        movement = StockMovement.objects.get(
            product=self.product,
            kind=StockMovement.Kind.ISSUE,
        )
        self.assertEqual(movement.actor_id, self.actor.pk)

    def test_duplicate_form_submission_changes_stock_once(self):
        data = self.payload()
        url = reverse("stock-receive", args=[self.product.pk])
        self.client.post(url, data)
        response = self.client.post(url, data, follow=True)
        self.assertContains(response, "already recorded")
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 10
        )

    def test_missing_reference_and_invalid_quantities_are_rejected(self):
        cases = [
            self.payload(operation_id=""),
            self.payload(quantity="0"),
            self.payload(quantity="-1"),
            self.payload(quantity="1.5"),
            self.payload(reason="   "),
        ]
        for data in cases:
            with self.subTest(data=data):
                response = self.client.post(
                    reverse("stock-receive", args=[self.product.pk]),
                    data,
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
        self.assertFalse(StockMovement.objects.exists())

    def test_insufficient_stock_retains_submission_reference(self):
        self.receive(quantity=3)
        data = self.payload(quantity="4")
        response = self.client.post(
            reverse("stock-issue", args=[self.product.pk]), data
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "exceeds the available stock")
        self.assertEqual(
            str(response.context["form"]["operation_id"].value()),
            data["operation_id"],
        )
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 3
        )

    def test_changed_duplicate_payload_is_rejected(self):
        data = self.payload()
        url = reverse("stock-receive", args=[self.product.pk])
        self.client.post(url, data)
        data["quantity"] = "11"
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "different stock request")
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity, 10
        )

    def test_viewer_cannot_write_stock_or_read_movement_history(self):
        self.client.force_login(self.viewer)
        self.assertEqual(
            self.client.get(reverse("stock-list")).status_code, 200
        )
        for name in ["stock-receive", "stock-issue"]:
            with self.subTest(action=name):
                response = self.client.post(
                    reverse(name, args=[self.product.pk]),
                    self.payload(),
                )
                self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.client.get(reverse("movement-list")).status_code, 403
        )
        self.assertFalse(StockMovement.objects.exists())

    def test_receive_permission_does_not_allow_issue_page(self):
        receiver = get_user_model().objects.create_user(
            username="web-receiver"
        )
        receiver.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="inventory",
                codename__in=["view_product", "receive_stock"],
            )
        )
        self.client.force_login(receiver)
        self.assertEqual(
            self.client.get(
                reverse("stock-receive", args=[self.product.pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                reverse("stock-issue", args=[self.product.pk]),
                self.payload(),
            ).status_code,
            403,
        )

    def test_stock_post_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        response = client.post(
            reverse("stock-receive", args=[self.product.pk]),
            self.payload(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(StockMovement.objects.exists())

    def test_dashboard_totals_and_stock_filters(self):
        low = Product.objects.create(
            name="Low Product", sku="LOW-001", reorder_level=5
        )
        healthy = Product.objects.create(
            name="Healthy Product", sku="HEALTHY-001", reorder_level=5
        )
        Product.objects.create(
            name="Archived Product", sku="ARCHIVED-001", is_active=False
        )
        self.receive(product=low, quantity=3)
        self.receive(product=healthy, quantity=6)

        response = self.client.get(reverse("overview"))
        self.assertEqual(
            response.context["stock_totals"],
            {"products": 3, "units": 9, "low": 1, "out": 1},
        )

        for health, expected in [
            ("low", low.pk),
            ("healthy", healthy.pk),
            ("out", self.product.pk),
        ]:
            with self.subTest(health=health):
                response = self.client.get(
                    reverse("stock-list"), {"health": health}
                )
                self.assertEqual(
                    [product.pk for product in response.context["page_obj"]],
                    [expected],
                )

    def test_stock_search_uses_sku(self):
        Product.objects.create(name="Other", sku="OTHER-WEB-001")
        response = self.client.get(
            reverse("stock-list"), {"q": self.product.sku}
        )
        self.assertEqual(
            [product.pk for product in response.context["page_obj"]],
            [self.product.pk],
        )

    def test_movement_search_and_kind_filter(self):
        self.receive()
        record_stock_movement(
            product_id=self.product.pk,
            actor=self.actor,
            kind=StockMovement.Kind.ISSUE,
            quantity=2,
            reason="Order SPECIAL-42",
            operation_id=uuid4(),
        )
        response = self.client.get(
            reverse("movement-list"),
            {"q": "SPECIAL-42", "kind": "issue"},
        )
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertContains(response, "Order SPECIAL-42")
        self.assertContains(response, "Stock issued")

    def test_invalid_movement_filter_returns_validation_error(self):
        self.receive()
        response = self.client.get(
            reverse("movement-list"), {"kind": "invalid"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["filter_form"].errors)
        self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_movement_history_is_paginated(self):
        for _ in range(21):
            self.receive(quantity=1)
        response = self.client.get(
            reverse("movement-list"), {"page": "1"}
        )
        self.assertEqual(len(response.context["page_obj"]), 20)
        response = self.client.get(
            reverse("movement-list"), {"page": "2"}
        )
        self.assertEqual(len(response.context["page_obj"]), 1)

    def test_product_with_stock_cannot_be_archived(self):
        self.receive(quantity=3)
        url = reverse("product-status", args=[self.product.pk])
        response = self.client.post(url, {"action": "archive"})
        self.assertRedirects(response, url)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)

        record_stock_movement(
            product_id=self.product.pk,
            actor=self.actor,
            kind=StockMovement.Kind.ISSUE,
            quantity=3,
            reason="Final units dispatched",
            operation_id=uuid4(),
        )
        response = self.client.post(url, {"action": "archive"})
        self.assertRedirects(response, reverse("product-list"))
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)

    def test_archived_product_rejects_new_stock_form_submission(self):
        Product.objects.filter(pk=self.product.pk).update(is_active=False)
        response = self.client.post(
            reverse("stock-receive", args=[self.product.pk]),
            self.payload(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Restore this product")
        self.assertFalse(StockMovement.objects.exists())

    def test_stale_product_form_does_not_restore_archived_product(self):
        form = ProductForm(
            {
                "name": "Updated product",
                "sku": self.product.sku,
                "category": "",
                "description": "",
                "unit_price": "1.00",
                "reorder_level": "5",
                "edit_token": edit_token(self.product),
            },
            instance=Product.objects.get(pk=self.product.pk),
        )
        self.assertTrue(form.is_valid())
        Product.objects.filter(pk=self.product.pk).update(is_active=False)
        save_product_form(form)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Updated product")
        self.assertFalse(self.product.is_active)
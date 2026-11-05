"""Tests for authentication and the product catalog."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from .forms import ProductForm
from .models import Product


class WorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="warehouse-user",
            email="warehouse@example.com",
            password="Workspace-test-password-482!",
        )

    def test_anonymous_visitor_is_redirected_to_login(self):
        response = self.client.get(reverse("overview"))
        self.assertRedirects(response, f"{reverse('login')}?next=/")

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
        cases = [
            {"unit_price": "-0.01"},
            {"reorder_level": "-1"},
            {"sku": "BAD SKU"},
            {"name": "   "},
        ]

        for override in cases:
            with self.subTest(override=override):
                form = ProductForm(self.product_data(**override))
                self.assertFalse(form.is_valid())

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
            name="Desk Lamp",
            sku="LAMP-001",
            category="Lighting",
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
            reverse("product-list"),
            {"status": "archived"},
        )
        self.assertContains(response, "USB-C Cable")

    def test_pagination_and_invalid_page(self):
        for number in range(13):
            Product.objects.create(
                name=f"Item {number:02d}",
                sku=f"ITEM-{number:02d}",
            )

        response = self.client.get(reverse("product-list"), {"page": "1"})
        self.assertEqual(len(response.context["page_obj"]), 12)

        response = self.client.get(reverse("product-list"), {"page": "2"})
        self.assertEqual(len(response.context["page_obj"]), 2)

        response = self.client.get(reverse("product-list"), {"page": "invalid"})
        self.assertEqual(response.context["page_obj"].number, 1)

    def test_overview_uses_actual_catalog_totals(self):
        Product.objects.create(
            name="Old Keyboard",
            sku="OLD-001",
            is_active=False,
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
            reverse("product-create"),
            self.product_data(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Product.objects.filter(sku="MOUSE-001").exists())

    def test_missing_product_returns_404(self):
        response = self.client.get(
            reverse("product-update", args=[999999])
        )
        self.assertEqual(response.status_code, 404)
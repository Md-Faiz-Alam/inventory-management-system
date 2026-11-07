"""Tests for supplier records, permissions, and product associations."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from inventory.models import Product, StockBalance

from config.editing import edit_token

from .forms import SupplierForm
from .models import Supplier
from .views import save_supplier_form


class SupplierTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()

        cls.admin = users.objects.create_superuser(
            username="supplier-admin",
            email="supplier-admin@example.com",
            password="Supplier-test-password-482!",
        )
        cls.member = users.objects.create_user(
            username="supplier-member"
        )
        cls.viewer = users.objects.create_user(
            username="supplier-viewer"
        )
        cls.editor = users.objects.create_user(
            username="supplier-editor"
        )

        cls.viewer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="suppliers",
                codename="view_supplier",
            )
        )
        cls.editor.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="suppliers",
                codename__in=[
                    "view_supplier",
                    "add_supplier",
                    "change_supplier",
                ],
            )
        )

        cls.product = Product.objects.create(
            name="Supplier Linked Product",
            sku="SUP-PRODUCT-001",
        )
        cls.archived_product = Product.objects.create(
            name="Archived Supplier Product",
            sku="SUP-PRODUCT-002",
            is_active=False,
        )
        cls.supplier = Supplier.objects.create(
            name="Example Supplies",
            code="SUP-001",
            contact_name="Contact Person",
            email="supplier@example.com",
            phone="+91 90000 00000",
        )
        cls.supplier.products.add(cls.product)

    def setUp(self):
        self.client.force_login(self.admin)

    def supplier_data(self, **overrides):
        data = {
            "name": "New Supplier",
            "code": "SUP-NEW",
            "contact_name": "New Contact",
            "email": "new@example.com",
            "phone": "+91 91111 11111",
            "address": "Example business address",
            "notes": "Delivery contact",
            "products": [str(self.product.pk)],
            "edit_token": edit_token(self.supplier),
        }
        data.update(overrides)
        return data

    def test_anonymous_pages_require_login(self):
        self.client.logout()

        pages = [
            ("supplier-list", []),
            ("supplier-create", []),
            ("supplier-detail", [self.supplier.pk]),
            ("supplier-update", [self.supplier.pk]),
            ("supplier-status", [self.supplier.pk]),
        ]

        for name, arguments in pages:
            with self.subTest(page=name):
                url = reverse(name, args=arguments)
                response = self.client.get(url)
                self.assertRedirects(
                    response,
                    f"{reverse('login')}?next={url}",
                )

    def test_member_cannot_read_or_write_suppliers(self):
        self.client.force_login(self.member)

        for name, arguments in [
            ("supplier-list", []),
            ("supplier-detail", [self.supplier.pk]),
            ("supplier-create", []),
            ("supplier-update", [self.supplier.pk]),
            ("supplier-status", [self.supplier.pk]),
        ]:
            with self.subTest(page=name):
                response = self.client.get(
                    reverse(name, args=arguments)
                )
                self.assertEqual(response.status_code, 403)

        response = self.client.post(
            reverse("supplier-create"),
            self.supplier_data(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Supplier.objects.count(), 1)

    def test_staff_flag_does_not_grant_supplier_access(self):
        self.member.is_staff = True
        self.member.save(update_fields=["is_staff"])
        self.client.force_login(self.member)

        response = self.client.get(reverse("supplier-list"))
        self.assertEqual(response.status_code, 403)

    def test_viewer_can_read_but_cannot_write(self):
        self.client.force_login(self.viewer)

        self.assertEqual(
            self.client.get(reverse("supplier-list")).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("supplier-detail", args=[self.supplier.pk])
            ).status_code,
            200,
        )

        for name, arguments, data in [
            ("supplier-create", [], self.supplier_data()),
            (
                "supplier-update",
                [self.supplier.pk],
                self.supplier_data(),
            ),
            (
                "supplier-status",
                [self.supplier.pk],
                {"action": "archive"},
            ),
        ]:
            with self.subTest(action=name):
                response = self.client.post(
                    reverse(name, args=arguments),
                    data,
                )
                self.assertEqual(response.status_code, 403)

        self.supplier.refresh_from_db()
        self.assertTrue(self.supplier.is_active)
        self.assertEqual(self.supplier.name, "Example Supplies")

    def test_create_normalizes_code_and_links_product(self):
        response = self.client.post(
            reverse("supplier-create"),
            self.supplier_data(code=" sup-new "),
        )
        supplier = Supplier.objects.get(code="SUP-NEW")

        self.assertRedirects(
            response,
            reverse("supplier-detail", args=[supplier.pk]),
        )
        self.assertEqual(
            list(supplier.products.all()),
            [self.product],
        )

    def test_duplicate_code_including_archived_is_rejected(self):
        for active in [True, False]:
            Supplier.objects.filter(pk=self.supplier.pk).update(
                is_active=active
            )
            form = SupplierForm(
                self.supplier_data(code="sup-001"),
                allow_products=True,
            )
            with self.subTest(active=active):
                self.assertFalse(form.is_valid())
                self.assertIn("code", form.errors)

    def test_invalid_fields_are_rejected(self):
        for override in [
            {"name": "   "},
            {"code": "BAD CODE"},
            {"code": ""},
            {"email": "not-an-email"},
            {"phone": "x" * 41},
            {"address": "x" * 1001},
            {"notes": "x" * 3001},
        ]:
            with self.subTest(override=override):
                form = SupplierForm(
                    self.supplier_data(**override),
                    allow_products=True,
                )
                self.assertFalse(form.is_valid())

    def test_database_rejects_case_insensitive_duplicate_code(self):
        other = Supplier.objects.create(
            name="Other Supplier",
            code="SUP-OTHER",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Supplier.objects.filter(pk=other.pk).update(
                    code="sup-001"
                )

    def test_database_rejects_empty_supplier_name(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Supplier.objects.filter(
                    pk=self.supplier.pk
                ).update(name="")

    def test_update_changes_contacts_and_can_clear_product_links(self):
        response = self.client.post(
            reverse("supplier-update", args=[self.supplier.pk]),
            self.supplier_data(
                code=self.supplier.code,
                name="Updated Supplier",
                products=[],
            ),
        )
        self.assertRedirects(
            response,
            reverse("supplier-detail", args=[self.supplier.pk]),
        )
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.name, "Updated Supplier")
        self.assertFalse(self.supplier.products.exists())

    def test_one_product_can_have_multiple_suppliers(self):
        other = Supplier.objects.create(
            name="Alternative Supplier",
            code="SUP-ALT",
        )
        other.products.add(self.product)

        supplier_ids = set(
            Supplier.objects.filter(
                products=self.product
            ).values_list("pk", flat=True)
        )

        self.assertEqual(
            supplier_ids,
            {self.supplier.pk, other.pk},
        )

    def test_unlinked_archived_product_cannot_be_selected(self):
        form = SupplierForm(
            self.supplier_data(
                products=[str(self.archived_product.pk)]
            ),
            allow_products=True,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("products", form.errors)

    def test_existing_archived_product_link_can_be_retained(self):
        self.supplier.products.add(self.archived_product)
        form = SupplierForm(
            self.supplier_data(
                code=self.supplier.code,
                products=[
                    str(self.product.pk),
                    str(self.archived_product.pk),
                ],
            ),
            instance=self.supplier,
            allow_products=True,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNotNone(save_supplier_form(form))
        self.assertEqual(self.supplier.products.count(), 2)

    def test_nonexistent_product_is_rejected(self):
        form = SupplierForm(
            self.supplier_data(products=["999999"]),
            allow_products=True,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("products", form.errors)

    def test_supplier_viewer_cannot_see_catalog_associations(self):
        self.client.force_login(self.viewer)

        response = self.client.get(
            reverse("supplier-detail", args=[self.supplier.pk])
        )
        self.assertNotIn("product_page", response.context)
        self.assertNotContains(response, self.product.name)
        self.assertNotContains(response, self.product.sku)

        response = self.client.get(reverse("supplier-list"))
        self.assertNotContains(response, '<th scope="col">Products</th>')

    def test_editor_without_catalog_access_cannot_change_links(self):
        self.client.force_login(self.editor)

        response = self.client.get(
            reverse("supplier-update", args=[self.supplier.pk])
        )
        self.assertNotIn("products", response.context["form"].fields)

        response = self.client.post(
            reverse("supplier-update", args=[self.supplier.pk]),
            self.supplier_data(
                code=self.supplier.code,
                name="Contact Updated",
                products=[str(self.archived_product.pk)],
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.name, "Contact Updated")
        self.assertEqual(
            list(self.supplier.products.all()),
            [self.product],
        )

    def test_editor_without_catalog_access_can_create_contacts_only(self):
        self.client.force_login(self.editor)
        response = self.client.post(
            reverse("supplier-create"),
            self.supplier_data(),
        )
        self.assertEqual(response.status_code, 302)

        supplier = Supplier.objects.get(code="SUP-NEW")
        self.assertFalse(supplier.products.exists())

    def test_archive_confirmation_does_not_change_supplier(self):
        response = self.client.get(
            reverse("supplier-status", args=[self.supplier.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.supplier.refresh_from_db()
        self.assertTrue(self.supplier.is_active)

    def test_archive_restore_preserves_links_and_stock(self):
        StockBalance.objects.create(
            product=self.product,
            quantity=7,
        )
        url = reverse("supplier-status", args=[self.supplier.pk])

        for action, expected_active in [
            ("archive", False),
            ("restore", True),
        ]:
            with self.subTest(action=action):
                response = self.client.post(url, {"action": action})
                self.assertRedirects(
                    response,
                    reverse("supplier-list"),
                )
                self.supplier.refresh_from_db()
                self.assertEqual(
                    self.supplier.is_active,
                    expected_active,
                )
                self.assertEqual(
                    list(self.supplier.products.all()),
                    [self.product],
                )
                self.assertEqual(
                    StockBalance.objects.get(
                        product=self.product
                    ).quantity,
                    7,
                )

    def test_invalid_status_action_is_rejected(self):
        response = self.client.post(
            reverse("supplier-status", args=[self.supplier.pk]),
            {"action": "delete"},
        )
        self.assertEqual(response.status_code, 400)
        self.supplier.refresh_from_db()
        self.assertTrue(self.supplier.is_active)

    def test_search_and_status_filters(self):
        Supplier.objects.create(
            name="Archived Business",
            code="SUP-OLD",
            is_active=False,
        )

        response = self.client.get(reverse("supplier-list"))
        self.assertContains(response, "Example Supplies")
        self.assertNotContains(response, "Archived Business")

        response = self.client.get(
            reverse("supplier-list"),
            {"status": "archived", "q": "SUP-OLD"},
        )
        self.assertContains(response, "Archived Business")
        self.assertNotContains(response, "Example Supplies")

        for query in [
            "SUP-001",
            "Contact Person",
            "supplier@example.com",
            "90000",
        ]:
            with self.subTest(query=query):
                response = self.client.get(
                    reverse("supplier-list"),
                    {"q": query},
                )
                self.assertEqual(
                    response.context["page_obj"].paginator.count,
                    1,
                )

    def test_invalid_status_defaults_to_active(self):
        Supplier.objects.create(
            name="Archived Business",
            code="SUP-OLD",
            is_active=False,
        )
        response = self.client.get(
            reverse("supplier-list"),
            {"status": "invalid"},
        )
        self.assertEqual(response.context["selected_status"], "active")
        self.assertNotContains(response, "Archived Business")

    def test_supplier_pagination(self):
        import warnings

        from django.core.paginator import UnorderedObjectListWarning

        for number in range(12):
            Supplier.objects.create(
                name="Repeated Supplier",
                code=f"SUP-PAGE-{number:02d}",
            )

        expected_ids = list(
            Supplier.objects.filter(is_active=True)
            .order_by("name", "pk")
            .values_list("pk", flat=True)
        )

        for user in [self.admin, self.viewer]:
            with self.subTest(username=user.username):
                self.client.force_login(user)

                with warnings.catch_warnings():
                    warnings.simplefilter(
                        "error",
                        UnorderedObjectListWarning,
                    )

                    first_response = self.client.get(
                        reverse("supplier-list"),
                        {"page": "1"},
                    )
                    second_response = self.client.get(
                        reverse("supplier-list"),
                        {"page": "2"},
                    )
                    invalid_response = self.client.get(
                        reverse("supplier-list"),
                        {"page": "invalid"},
                    )

                first_page = first_response.context["page_obj"]
                second_page = second_response.context["page_obj"]

                self.assertTrue(first_page.paginator.object_list.ordered)
                self.assertEqual(len(first_page), 12)
                self.assertEqual(len(second_page), 1)

                actual_ids = [
                    supplier.pk
                    for supplier in first_page
                ] + [
                    supplier.pk
                    for supplier in second_page
                ]

                self.assertEqual(actual_ids, expected_ids)
                self.assertEqual(
                    invalid_response.context["page_obj"].number,
                    1,
                )

    def test_missing_supplier_returns_404(self):
        for name in [
            "supplier-detail",
            "supplier-update",
            "supplier-status",
        ]:
            with self.subTest(page=name):
                response = self.client.get(
                    reverse(name, args=[999999])
                )
                self.assertEqual(response.status_code, 404)

    def test_write_endpoints_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)

        for name, arguments, data in [
            ("supplier-create", [], self.supplier_data()),
            (
                "supplier-update",
                [self.supplier.pk],
                self.supplier_data(code=self.supplier.code),
            ),
            (
                "supplier-status",
                [self.supplier.pk],
                {"action": "archive"},
            ),
        ]:
            with self.subTest(action=name):
                response = client.post(
                    reverse(name, args=arguments),
                    data,
                )
                self.assertEqual(response.status_code, 403)

        self.assertEqual(Supplier.objects.count(), 1)
        self.supplier.refresh_from_db()
        self.assertTrue(self.supplier.is_active)

    def test_stale_edit_does_not_restore_archived_supplier(self):
        form = SupplierForm(
            self.supplier_data(
                code=self.supplier.code,
                name="Updated After Archive",
            ),
            instance=Supplier.objects.get(pk=self.supplier.pk),
            allow_products=True,
        )
        self.assertTrue(form.is_valid(), form.errors)

        Supplier.objects.filter(pk=self.supplier.pk).update(
            is_active=False
        )
        self.assertIsNotNone(save_supplier_form(form))

        self.supplier.refresh_from_db()
        self.assertFalse(self.supplier.is_active)
        self.assertEqual(
            self.supplier.name,
            "Updated After Archive",
        )

    def test_duplicate_code_race_returns_form_error(self):
        form = SupplierForm(
            self.supplier_data(),
            allow_products=True,
        )
        self.assertTrue(form.is_valid(), form.errors)

        Supplier.objects.create(
            name="Competing Supplier",
            code="SUP-NEW",
        )
        self.assertIsNone(save_supplier_form(form))
        self.assertIn("code", form.errors)
        self.assertEqual(
            Supplier.objects.filter(code="SUP-NEW").count(),
            1,
        )

    def test_link_failure_rolls_back_contact_changes(self):
        form = SupplierForm(
            self.supplier_data(
                code=self.supplier.code,
                name="Must Roll Back",
                products=[],
            ),
            instance=self.supplier,
            allow_products=True,
        )
        self.assertTrue(form.is_valid(), form.errors)

        with patch.object(
            form,
            "save_m2m",
            side_effect=RuntimeError("Simulated link failure"),
            create=True,
        ):
            # save(commit=False) installs save_m2m, so patch its target.
            with patch.object(
                form,
                "_save_m2m",
                side_effect=RuntimeError("Simulated link failure"),
            ):
                with self.assertRaises(RuntimeError):
                    save_supplier_form(form)

        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.name, "Example Supplies")
        self.assertEqual(
            list(self.supplier.products.all()),
            [self.product],
        )

    def test_supplier_content_is_escaped(self):
        self.supplier.notes = '<script>alert("supplier")</script>'
        self.supplier.save(update_fields=["notes"])

        response = self.client.get(
            reverse("supplier-detail", args=[self.supplier.pk])
        )
        self.assertNotContains(
            response,
            '<script>alert("supplier")</script>',
        )
        self.assertContains(response, "&lt;script&gt;")

    def test_supplier_navigation_requires_permission(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("overview"))
        self.assertNotContains(
            response,
            f'href="{reverse("supplier-list")}"',
        )

        self.client.force_login(self.viewer)
        response = self.client.get(reverse("overview"))
        self.assertContains(
            response,
            f'href="{reverse("supplier-list")}"',
        )

    def test_supplier_pages_are_not_cached(self):
        response = self.client.get(reverse("supplier-list"))
        self.assertIn("no-store", response.headers["Cache-Control"])
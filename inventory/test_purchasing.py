"""Purchase orders, receipts, reversals and stocktakes through the web pages."""

from decimal import Decimal
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from accounts.roles import MANAGER, STAFF
from audit.models import AuditEntry
from config.testing import make_product, make_supplier, make_user, receive

from .models import PurchaseOrder, StockBalance, StockMovement
from .services import StockError, record_stocktake


class PurchaseOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = make_user("manager", MANAGER)
        cls.staff = make_user("staff", STAFF)
        cls.product = make_product()
        cls.supplier = make_supplier()
        cls.supplier.products.add(cls.product)

    def setUp(self):
        self.client.force_login(self.manager)

    def create_order(self, quantity=10):
        response = self.client.post(
            reverse("po-create"),
            {
                "reference": "po-test-1",
                "supplier": self.supplier.pk,
                "product": self.product.pk,
                "quantity": quantity,
                "unit_cost": "55.00",
            },
        )
        order = PurchaseOrder.objects.get()
        self.assertRedirects(response, reverse("po-detail", args=[order.pk]))
        return order

    def receive_against(self, order, quantity, document="DC-1"):
        return self.client.post(
            reverse("po-receive", args=[order.pk]),
            {
                "operation_id": str(uuid4()),
                "quantity": quantity,
                "document_reference": document,
                "reason": "Delivery",
            },
        )

    def test_create_prefills_from_product_and_normalises_reference(self):
        response = self.client.get(reverse("po-create"), {"product": self.product.pk})
        self.assertEqual(response.context["form"].initial["supplier"], self.supplier.pk)
        order = self.create_order()
        self.assertEqual(order.reference, "PO-TEST-1")
        self.assertEqual(order.actor, self.manager)
        self.assertTrue(
            AuditEntry.objects.filter(action="create", object_id=str(order.pk)).exists()
        )

    def test_partial_then_full_receipt_updates_stock_and_status(self):
        order = self.create_order(quantity=10)
        self.receive_against(order, 4)
        order.refresh_from_db()
        self.assertEqual(order.status, "Partially received")
        self.assertEqual(StockBalance.objects.get(product=self.product).quantity, 4)

        self.receive_against(order, 6, document="DC-2")
        order.refresh_from_db()
        self.assertEqual(order.status, "Complete")
        movement = StockMovement.objects.filter(purchase_order=order).last()
        assert movement is not None
        self.assertEqual(movement.unit_cost, Decimal("55.00"))

    def test_receipt_cannot_exceed_outstanding(self):
        order = self.create_order(quantity=5)
        response = self.receive_against(order, 6)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "exceeds the outstanding order")
        self.assertFalse(StockMovement.objects.exists())

    def test_cancelled_order_rejects_receipts(self):
        order = self.create_order()
        self.client.post(reverse("po-cancel", args=[order.pk]))
        order.refresh_from_db()
        self.assertTrue(order.cancelled)
        response = self.receive_against(order, 1)
        self.assertContains(response, "cancelled")

    def test_staff_can_receive_but_not_create_or_correct(self):
        order = self.create_order()
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("po-create")).status_code, 403)
        self.assertEqual(self.receive_against(order, 2).status_code, 302)
        self.assertNotContains(self.client.get(reverse("po-detail", args=[order.pk])), "Cancel order")
        self.assertEqual(self.client.post(reverse("po-cancel", args=[order.pk])).status_code, 403)
        order.refresh_from_db()
        self.assertFalse(order.cancelled)
        movement = StockMovement.objects.get()
        self.assertEqual(
            self.client.get(reverse("movement-reverse", args=[movement.pk])).status_code,
            403,
        )

    def test_reversing_a_po_receipt_restores_outstanding(self):
        order = self.create_order(quantity=10)
        self.receive_against(order, 10)
        movement = StockMovement.objects.get()
        response = self.client.post(
            reverse("movement-reverse", args=[movement.pk]),
            {"operation_id": str(uuid4()), "reason": "Wrong delivery"},
        )
        self.assertRedirects(response, reverse("movement-list"))
        order.refresh_from_db()
        self.assertEqual(order.received_quantity, 0)
        self.assertEqual(StockBalance.objects.get(product=self.product).quantity, 0)

        # A second reversal is refused.
        response = self.client.get(reverse("movement-reverse", args=[movement.pk]))
        self.assertContains(response, "already been reversed")

    def test_po_pdf_downloads(self):
        order = self.create_order()
        response = self.client.get(reverse("po-pdf", args=[order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))


class StocktakeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = make_user("counter", MANAGER)
        cls.product = make_product()

    def test_stocktake_adjusts_to_counted_quantity(self):
        receive(self.product, self.manager, 10)
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("stocktake", args=[self.product.pk]),
            {"operation_id": str(uuid4()), "counted_quantity": 7, "reason": "Monthly count"},
        )
        self.assertRedirects(response, reverse("stock-list"))
        self.assertEqual(StockBalance.objects.get(product=self.product).quantity, 7)
        movement = StockMovement.objects.latest("pk")
        self.assertEqual((movement.kind, movement.quantity, movement.counted_quantity), ("issue", 3, 7))

    def test_matching_count_is_rejected(self):
        receive(self.product, self.manager, 4)
        with self.assertRaises(StockError):
            record_stocktake(
                product_id=self.product.pk,
                actor=self.manager,
                counted_quantity=4,
                reason="Count",
                operation_id=uuid4(),
            )


class SalesReversalGuardTests(TestCase):
    def test_sales_fulfilment_movement_cannot_be_reversed(self):
        from sales.models import Customer, SalesOrder, SalesOrderLine
        from sales.services import confirm_order, fulfil_order

        manager = make_user("guard", MANAGER)
        product = make_product()
        receive(product, manager, 5)
        order = SalesOrder.objects.create(
            number="SO-T-1", customer=Customer.objects.create(name="C", code="C1"), actor=manager
        )
        SalesOrderLine.objects.create(order=order, product=product, quantity=2, unit_price="1.00")
        confirm_order(order_id=order.pk, actor=manager)
        fulfil_order(order_id=order.pk, actor=manager)
        movement = StockMovement.objects.get(sales_line__order=order)

        self.client.force_login(manager)
        response = self.client.post(
            reverse("movement-reverse", args=[movement.pk]),
            {"operation_id": str(uuid4()), "reason": "Oops"},
        )
        self.assertContains(response, "fulfilled a sales order")
        self.assertEqual(StockBalance.objects.get(product=product).quantity, 3)


class LandingPageTests(TestCase):
    def test_landing_shows_live_counts(self):
        make_product()
        response = self.client.get(reverse("overview"))
        self.assertEqual(response.context["showcase"]["products"], 1)
        self.assertContains(response, "LIVE FROM THE DEMO DATABASE")


class BootstrapDemoTests(TestCase):
    def test_reuses_catalog_seeded_by_another_user(self):
        import os
        from io import StringIO
        from unittest.mock import patch

        from django.core.management import call_command

        owner = make_user("owner", is_superuser=True)
        call_command("seed_demo", actor=owner.username, stdout=StringIO())
        out = StringIO()
        with patch.dict(os.environ, {"DEMO_ADMIN_PASSWORD": "Demo-admin-pass-4410!"}):
            call_command("bootstrap_demo", stdout=out)
        self.assertIn("Demo catalog already present", out.getvalue())
        self.assertTrue(PurchaseOrder.objects.filter(reference="PO-DEMO-001").exists())


class BootstrapAccountsTests(TestCase):
    def test_guest_and_manager_only_setup_keeps_owner_untouched(self):
        import os
        from io import StringIO
        from unittest.mock import patch

        from django.core.management import call_command

        from accounts.models import User
        from accounts.roles import role_of

        owner = make_user("owner", is_superuser=True, is_staff=True)
        env = {
            "DEMO_MANAGER_PASSWORD": "Demo-manager-pass-4410!",
            "DJANGO_SUPERUSER_USERNAME": "owner",
            "DJANGO_SUPERUSER_PASSWORD": "Something-else-entirely-1!",
        }
        with patch.dict(os.environ, env), self.settings(GUEST_ACCESS=True):
            call_command("bootstrap_demo", stdout=StringIO())

        guest = User.objects.get(username="demo_guest")
        self.assertFalse(guest.has_usable_password())
        self.assertEqual(role_of(guest), "Viewer")
        self.assertFalse(User.objects.filter(username="demo_admin").exists())
        # The owner's own password was not replaced by the env value.
        owner.refresh_from_db()
        self.assertTrue(owner.check_password("Workspace-test-password-482!"))
        # Demo data was recorded by the demo manager, not the owner.
        self.assertTrue(PurchaseOrder.objects.filter(actor__username="demo_manager").exists())


class GuestOnlyPublicModeTests(TestCase):
    def test_removed_demo_passwords_switch_accounts_off_and_owner_loads_data(self):
        import os
        from io import StringIO
        from unittest.mock import patch

        from django.core.management import call_command

        from accounts.models import User
        from accounts.roles import STAFF

        published = make_user("demo_staff", STAFF)  # had a password once
        env = {
            "DJANGO_SUPERUSER_USERNAME": "owner",
            "DJANGO_SUPERUSER_PASSWORD": "Owner-only-pass-9931!",
        }
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("DEMO_")}
        with patch.dict(os.environ, {**clean_env, **env}, clear=True), self.settings(
            GUEST_ACCESS=True, DEMO_MODE=True, DEMO_LOGINS=[]
        ):
            out = StringIO()
            call_command("bootstrap_demo", stdout=out)

        self.assertFalse(User.objects.get(pk=published.pk).is_active)
        self.assertIn("Deactivated demo_staff", out.getvalue())
        self.assertTrue(PurchaseOrder.objects.filter(actor__username="owner").exists())
        self.assertTrue(User.objects.get(username="demo_guest").is_active)

        # Public page offers the guest button only.
        with self.settings(GUEST_ACCESS=True, DEMO_MODE=True, DEMO_LOGINS=[]):
            page = self.client.get(reverse("overview"))
        self.assertContains(page, "Explore as guest")
        self.assertNotContains(page, "Try the live demo")

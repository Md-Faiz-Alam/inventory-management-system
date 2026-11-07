"""Customers and the sales order workflow."""

from django.test import TestCase
from django.urls import reverse

from accounts.roles import MANAGER, STAFF
from config.editing import edit_token
from config.testing import make_product, make_user, receive
from inventory.models import StockBalance, StockMovement

from .models import Customer, SalesOrder, SalesOrderLine
from .services import SalesError, fulfil_order, next_order_number


class SalesWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = make_user("sales-manager", MANAGER)
        cls.staff = make_user("sales-staff", STAFF)
        cls.cable = make_product("CABLE-1", "Cable", "250.00")
        cls.mouse = make_product("MOUSE-1", "Mouse", "800.00")
        receive(cls.cable, cls.manager, 10)
        receive(cls.mouse, cls.manager, 2)
        cls.customer = Customer.objects.create(name="Acme", code="cust-1")

    def setUp(self):
        self.client.force_login(self.manager)

    def post_order(self, lines):
        data = {
            "customer": self.customer.pk,
            "notes": "",
            "lines-TOTAL_FORMS": str(len(lines)),
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
        }
        for index, (product, quantity, price) in enumerate(lines):
            data[f"lines-{index}-product"] = product.pk
            data[f"lines-{index}-quantity"] = quantity
            data[f"lines-{index}-unit_price"] = price
        return self.client.post(reverse("order-create"), data)

    def test_customer_code_is_normalised(self):
        self.assertEqual(self.customer.code, "CUST-1")

    def test_create_order_uses_catalog_price_when_blank(self):
        response = self.post_order([(self.cable, 3, ""), (self.mouse, 1, "750.00")])
        order = SalesOrder.objects.get()
        self.assertRedirects(response, reverse("order-detail", args=[order.pk]))
        self.assertTrue(order.number.startswith("SO-"))
        prices = dict(order.lines.values_list("product__sku", "unit_price"))
        self.assertEqual(str(prices["CABLE-1"]), "250.00")
        self.assertEqual(str(prices["MOUSE-1"]), "750.00")
        self.assertEqual(str(order.total), "1500.00")

    def test_duplicate_products_are_rejected(self):
        response = self.post_order([(self.cable, 1, ""), (self.cable, 2, "")])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "duplicate values")
        self.assertFalse(SalesOrder.objects.exists())

    def test_fulfilment_issues_every_line(self):
        self.post_order([(self.cable, 3, ""), (self.mouse, 2, "")])
        order = SalesOrder.objects.get()
        self.client.post(reverse("order-action", args=[order.pk]), {"action": "confirm"})
        self.client.post(reverse("order-action", args=[order.pk]), {"action": "fulfil"})
        order.refresh_from_db()
        self.assertEqual(order.status, "fulfilled")
        self.assertEqual(StockBalance.objects.get(product=self.cable).quantity, 7)
        self.assertEqual(StockBalance.objects.get(product=self.mouse).quantity, 0)
        self.assertEqual(SalesOrderLine.objects.filter(movement__isnull=False).count(), 2)

    def test_shortage_rolls_back_the_whole_order(self):
        self.post_order([(self.cable, 3, ""), (self.mouse, 5, "")])
        order = SalesOrder.objects.get()
        self.client.post(reverse("order-action", args=[order.pk]), {"action": "confirm"})
        movements_before = StockMovement.objects.count()
        response = self.client.post(
            reverse("order-action", args=[order.pk]), {"action": "fulfil"}, follow=True
        )
        self.assertContains(response, "exceeds the available stock")
        order.refresh_from_db()
        self.assertEqual(order.status, "confirmed")
        self.assertEqual(StockMovement.objects.count(), movements_before)
        self.assertEqual(StockBalance.objects.get(product=self.cable).quantity, 10)

    def test_fulfil_requires_confirmation_and_is_idempotent(self):
        self.post_order([(self.cable, 1, "")])
        order = SalesOrder.objects.get()
        with self.assertRaises(SalesError):
            fulfil_order(order_id=order.pk, actor=self.manager)
        self.client.post(reverse("order-action", args=[order.pk]), {"action": "confirm"})
        fulfil_order(order_id=order.pk, actor=self.manager)
        fulfil_order(order_id=order.pk, actor=self.manager)
        self.assertEqual(StockBalance.objects.get(product=self.cable).quantity, 9)

    def test_staff_cannot_confirm_or_edit_customers(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("customer-create")).status_code, 403)
        self.post_order([(self.cable, 1, "")])
        order = SalesOrder.objects.get()
        response = self.client.post(reverse("order-action", args=[order.pk]), {"action": "confirm"})
        self.assertEqual(response.status_code, 403)

    def test_invoice_pdf(self):
        self.post_order([(self.cable, 1, "")])
        order = SalesOrder.objects.get()
        response = self.client.get(reverse("order-invoice", args=[order.pk]))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("quotation", response["Content-Disposition"])

    def test_order_numbers_are_sequential(self):
        first = next_order_number()
        SalesOrder.objects.create(number=first, customer=self.customer, actor=self.manager)
        self.assertEqual(int(next_order_number()[-5:]), int(first[-5:]) + 1)

    def test_customer_edit_requires_fresh_token(self):
        url = reverse("customer-update", args=[self.customer.pk])
        data = {"name": "Acme Ltd", "code": "CUST-1", "email": "", "phone": "", "gstin": "", "address": "", "notes": ""}
        stale = edit_token(self.customer)
        Customer.objects.filter(pk=self.customer.pk).update(name="Changed elsewhere")
        Customer.objects.get(pk=self.customer.pk).save()  # bumps updated_at
        response = self.client.post(url, {**data, "edit_token": stale})
        self.assertContains(response, "changed after you opened it")

        fresh = edit_token(Customer.objects.get(pk=self.customer.pk))
        response = self.client.post(url, {**data, "edit_token": fresh})
        self.assertRedirects(response, reverse("customer-detail", args=[self.customer.pk]))

    def test_live_search_returns_partial(self):
        response = self.client.get(
            reverse("customer-list"), {"q": "acme"}, HTTP_HX_REQUEST="true"
        )
        self.assertTemplateUsed(response, "sales/_customer_rows.html")
        self.assertTemplateNotUsed(response, "sales/customer_list.html")
        self.assertContains(response, "Acme")

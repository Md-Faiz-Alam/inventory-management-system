"""Single-statement report reads and PostgreSQL concurrency regressions."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from typing import cast
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.db.models import Count, Sum
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse

from inventory.models import MAX_STOCK_QUANTITY, Product, StockBalance, StockMovement
from inventory.services import record_stock_movement
from inventory.views import products_with_stock

from .snapshots import snapshot_page
from .views import movement_report, stock_report


class SnapshotPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username="snapshot-reader",
            email="reader@example.com",
            password="Snapshot-reader-password-482!",
        )

        for number in range(1, 28):
            product = Product.objects.create(
                name="Same product name",
                sku=f"SNAP-{number:03d}",
            )
            StockBalance.objects.create(
                product=product,
                quantity=number,
            )
            StockMovement.objects.create(
                product=product,
                actor=cls.admin,
                operation_id=uuid4(),
                kind="receive",
                quantity=number,
                reason="Report test fixture",
                balance_before=0,
                balance_after=number,
            )

    def records(self):
        return (
            products_with_stock()
            .filter(is_active=True)
            .values("sku", "name", "on_hand")
        )

    def read_page(self, records=None, number="1"):
        if records is None:
            records = self.records()

        return snapshot_page(
            records,
            summary_fields={
                "Products": Count("pk"),
                "Units": Sum("on_hand"),
            },
            order_by=("name", "pk"),
            page_number=number,
            page_size=25,
        )

    def test_totals_count_and_page_rows_use_one_statement(self):
        with self.assertNumQueries(1):
            page, summary = self.read_page(number="2")
            rows = list(page)

            self.assertEqual(page.paginator.count, 27)
            self.assertEqual(
                summary,
                {"Products": 27, "Units": 378},
            )

        self.assertEqual(
            [row["sku"] for row in rows],
            ["SNAP-026", "SNAP-027"],
        )
        self.assertEqual(page.number, 2)
        self.assertEqual(page.start_index(), 26)
        self.assertEqual(page.end_index(), 27)
        self.assertTrue(page.has_previous())
        self.assertFalse(page.has_next())

    def test_filters_apply_to_totals_and_rows(self):
        with self.assertNumQueries(1):
            page, summary = self.read_page(
                self.records().filter(on_hand__lte=5)
            )

        self.assertEqual(summary, {"Products": 5, "Units": 15})
        self.assertEqual(len(page), 5)
        self.assertEqual(page.paginator.count, 5)

    def test_empty_result_has_zero_totals_and_page_one(self):
        with self.assertNumQueries(1):
            page, summary = self.read_page(
                self.records().filter(sku="MISSING"),
                number="999",
            )

        self.assertEqual(summary, {"Products": 0, "Units": 0})
        self.assertEqual(list(page), [])
        self.assertEqual(page.number, 1)
        self.assertEqual(page.paginator.count, 0)

    def test_invalid_and_out_of_range_pages_preserve_navigation(self):
        for value, expected in [
            (None, 1),
            ("invalid", 1),
            ("1.5", 1),
            ("0", 2),
            ("-1", 2),
            ("999", 2),
            ("9" * 1000, 2),
        ]:
            with self.subTest(value=value), self.assertNumQueries(1):
                page, summary = self.read_page(number=value)

                self.assertEqual(page.number, expected)
                self.assertEqual(summary["Products"], 27)

    def test_large_integer_summary_stays_exact(self):
        StockBalance.objects.filter(
            product__sku="SNAP-001"
        ).update(quantity=MAX_STOCK_QUANTITY)

        with self.assertNumQueries(1):
            page, summary = self.read_page(
                self.records().filter(sku="SNAP-001")
            )

        self.assertEqual(summary["Units"], MAX_STOCK_QUANTITY)
        self.assertEqual(page[0]["on_hand"], MAX_STOCK_QUANTITY)

    def test_html_and_csv_views_each_read_report_data_once(self):
        factory = RequestFactory()

        for view, name in [
            (stock_report, "report-stock"),
            (movement_report, "report-movements"),
        ]:
            for output in ["html", "csv"]:
                request = factory.get(
                    reverse(name),
                    {"format": output},
                )
                request.user = self.admin

                with self.subTest(
                    report=name,
                    output=output,
                ), self.assertNumQueries(1):
                    response = view(request)

                self.assertEqual(response.status_code, 200)


class ReportSnapshotConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(
            connection.vendor,
            "postgresql",
            "Report concurrency tests require PostgreSQL.",
        )

        self.admin = get_user_model().objects.create_superuser(
            username="snapshot-writer",
            email="writer@example.com",
            password="Snapshot-writer-password-482!",
        )
        self.product = Product.objects.create(
            name="Existing product",
            sku="SNAP-EXISTING",
        )
        Product.objects.create(
            name="Empty product",
            sku="SNAP-EMPTY",
        )

        record_stock_movement(
            product_id=self.product.pk,
            actor=self.admin,
            kind="receive",
            quantity=10,
            reason="Opening receipt",
            operation_id=uuid4(),
        )
        record_stock_movement(
            product_id=self.product.pk,
            actor=self.admin,
            kind="issue",
            quantity=3,
            reason="Opening issue",
            operation_id=uuid4(),
        )

        self.client.force_login(self.admin)

    def commit_stock_changes(self):
        close_old_connections()

        try:
            actor = get_user_model().objects.get(pk=self.admin.pk)

            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
                cursor.execute("SET statement_timeout = '10s'")

            with transaction.atomic():
                record_stock_movement(
                    product_id=self.product.pk,
                    actor=actor,
                    kind="receive",
                    quantity=2,
                    reason="Concurrent receipt",
                    operation_id=uuid4(),
                )

                new_product = Product.objects.create(
                    name="Concurrent product",
                    sku="SNAP-NEW",
                )

                record_stock_movement(
                    product_id=new_product.pk,
                    actor=actor,
                    kind="receive",
                    quantity=4,
                    reason="Concurrent new product receipt",
                    operation_id=uuid4(),
                )
        finally:
            connection.close()

    def read_during_commit(self, name):
        committed = False

        with ThreadPoolExecutor(max_workers=1) as executor:

            def wrapper(execute, sql, params, many, context):
                nonlocal committed

                result = execute(sql, params, many, context)

                if (
                    not committed
                    and "_report_position" in sql
                    and sql.lstrip().upper().startswith("SELECT")
                ):
                    committed = True
                    executor.submit(
                        self.commit_stock_changes
                    ).result(timeout=20)

                return result

            # Django decorates execute_wrapper with @contextmanager.
            wrapper_context = cast(
                AbstractContextManager[None],
                connection.execute_wrapper(wrapper),
            )

            with wrapper_context:
                response = self.client.get(reverse(name))

        self.assertTrue(
            committed,
            "The concurrent write must complete before report rendering.",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            StockBalance.objects.get(product=self.product).quantity,
            9,
        )
        self.assertTrue(
            Product.objects.filter(sku="SNAP-NEW").exists()
        )

        return response

    def test_stock_response_keeps_one_snapshot_during_committed_writes(self):
        response = self.read_during_commit("report-stock")

        self.assertEqual(
            response.context["summary"],
            {
                "Products": 2,
                "Units on hand": 7,
                "Low stock": 0,
                "Out of stock": 1,
            },
        )
        self.assertEqual(
            response.context["page_obj"].paginator.count,
            2,
        )

        rows = {
            row[0]: row
            for row in response.context["rows"]
        }

        self.assertEqual(rows["SNAP-EXISTING"][3], 7)
        self.assertNotIn("SNAP-NEW", rows)

        refreshed = self.client.get(reverse("report-stock"))

        self.assertEqual(
            refreshed.context["summary"]["Products"],
            3,
        )
        self.assertEqual(
            refreshed.context["summary"]["Units on hand"],
            13,
        )

    def test_movement_response_keeps_one_snapshot_during_committed_writes(self):
        response = self.read_during_commit("report-movements")

        self.assertEqual(
            response.context["summary"],
            {
                "Movements": 2,
                "Units received": 10,
                "Units issued": 3,
                "Net units": 7,
            },
        )
        self.assertEqual(
            response.context["page_obj"].paginator.count,
            2,
        )
        self.assertEqual(
            len(response.context["rows"]),
            2,
        )
        self.assertEqual(
            {row[1] for row in response.context["rows"]},
            {"SNAP-EXISTING"},
        )

        refreshed = self.client.get(reverse("report-movements"))

        self.assertEqual(
            refreshed.context["summary"],
            {
                "Movements": 4,
                "Units received": 16,
                "Units issued": 3,
                "Net units": 13,
            },
        )
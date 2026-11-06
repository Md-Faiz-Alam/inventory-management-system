"""Tests for repeatable demo plans and additive PostgreSQL seeding."""

from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from suppliers.models import Supplier

from .management.commands.seed_demo import demo_plan
from .models import Product, StockBalance, StockMovement
from .services import record_stock_movement
from .views import products_with_stock


class DemoPlanTests(SimpleTestCase):
    def test_plan_covers_health_states_and_archival(self):
        plan = demo_plan()
        active = [
            item
            for item in plan
            if not item["archived"]
        ]

        self.assertEqual(len(plan), 32)
        self.assertEqual(len(active), 30)
        self.assertEqual(
            sum(item["target"] for item in active),
            199,
        )
        self.assertEqual(
            sum(item["target"] == 0 for item in active),
            8,
        )
        self.assertEqual(
            sum(
                0 < item["target"] <= 5
                for item in active
            ),
            15,
        )
        self.assertEqual(
            sum(item["target"] > 5 for item in active),
            7,
        )
        self.assertTrue(
            all(
                item["target"] == 0
                for item in plan
                if item["archived"]
            )
        )

    def test_operation_ids_are_stable_and_unique(self):
        ids = [
            move["operation_id"]
            for item in demo_plan()
            for move in item["movements"]
        ]
        repeated = [
            move["operation_id"]
            for item in demo_plan()
            for move in item["movements"]
        ]

        self.assertEqual(ids, repeated)
        self.assertEqual(len(ids), 256)
        self.assertEqual(len(set(ids)), 256)

    def test_every_planned_history_stays_nonnegative(self):
        for item in demo_plan():
            balance = 0

            for move in item["movements"]:
                self.assertGreater(move["quantity"], 0)
                balance += (
                    move["quantity"]
                    if move["kind"] == "receive"
                    else -move["quantity"]
                )
                self.assertGreaterEqual(balance, 0)

            self.assertEqual(
                balance,
                item["target"],
            )

    def test_data_is_clearly_synthetic_and_spans_dates(self):
        plan = demo_plan()

        self.assertEqual(
            len({item["category"] for item in plan}),
            6,
        )
        self.assertEqual(
            len({item["sku"] for item in plan}),
            32,
        )
        self.assertTrue(
            all(
                item["name"].startswith("Demo ")
                for item in plan
            )
        )
        self.assertTrue(
            all(
                isinstance(item["unit_price"], Decimal)
                for item in plan
            )
        )
        self.assertEqual(
            {
                move["day"]
                for move in plan[0]["movements"]
            },
            {0, 4, 8, 12, 16, 20, 24, 28},
        )


class DemoCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username="demo-seed-admin",
            email="demo-admin@example.com",
            password="Demo-seed-test-password-482!",
        )
        cls.member = get_user_model().objects.create_user(
            username="demo-seed-member"
        )

    def setUp(self):
        self.assertEqual(
            connection.vendor,
            "postgresql",
            "Run demo command integration tests against PostgreSQL.",
        )

    def seed(self, **options):
        output = StringIO()

        call_command(
            "seed_demo",
            actor=options.pop(
                "actor",
                self.admin.username,
            ),
            stdout=output,
            **options,
        )
        return output.getvalue()

    def counts(self):
        return (
            Product.objects.count(),
            Supplier.objects.count(),
            StockBalance.objects.count(),
            StockMovement.objects.count(),
        )

    def test_dry_run_writes_nothing(self):
        before = self.counts()

        self.assertIn(
            "Would create",
            self.seed(dry_run=True),
        )
        self.assertEqual(
            self.counts(),
            before,
        )

    def test_seed_preserves_existing_business_data(self):
        product = Product.objects.create(
            name="Business Product",
            sku="BUSINESS-001",
        )
        supplier = Supplier.objects.create(
            name="Business Supplier",
            code="BUSINESS-SUP",
        )
        supplier.products.add(product)

        original = record_stock_movement(
            product_id=product.pk,
            actor=self.admin,
            kind="receive",
            quantity=11,
            reason="Actual business fixture",
            operation_id=uuid4(),
        ).movement

        self.seed()

        self.assertEqual(
            self.counts(),
            (33, 7, 33, 257),
        )
        product.refresh_from_db()
        self.assertEqual(
            product.name,
            "Business Product",
        )
        self.assertEqual(
            StockBalance.objects.get(
                product=product
            ).quantity,
            11,
        )
        self.assertTrue(
            StockMovement.objects.filter(
                pk=original.pk,
                reason="Actual business fixture",
            ).exists()
        )
        self.assertEqual(
            list(supplier.products.all()),
            [product],
        )
        self.assertEqual(
            get_user_model().objects.count(),
            2,
        )

    def test_seed_has_expected_stock_states_and_links(self):
        self.seed()

        self.assertEqual(
            self.counts(),
            (32, 6, 32, 256),
        )
        self.assertEqual(
            Supplier.objects.filter(
                is_active=False
            ).count(),
            1,
        )
        self.assertEqual(
            sum(
                supplier.products.count()
                for supplier in Supplier.objects.all()
            ),
            64,
        )

        active = list(
            products_with_stock()
            .filter(is_active=True)
            .values_list("on_hand", flat=True)
        )

        self.assertEqual(len(active), 30)
        self.assertEqual(sum(active), 199)
        self.assertEqual(
            sum(quantity == 0 for quantity in active),
            8,
        )
        self.assertEqual(
            sum(
                0 < quantity <= 5
                for quantity in active
            ),
            15,
        )
        self.assertEqual(
            sum(quantity > 5 for quantity in active),
            7,
        )
        self.assertEqual(
            StockMovement.objects.filter(
                kind="receive"
            ).count(),
            128,
        )
        self.assertEqual(
            StockMovement.objects.filter(
                kind="issue"
            ).count(),
            128,
        )
        self.assertFalse(
            products_with_stock().filter(
                is_active=False,
                on_hand__gt=0,
            ).exists()
        )

    def test_seed_histories_match_balances_and_dates(self):
        self.seed()
        now = timezone.now()

        for product in Product.objects.all():
            movements = list(
                StockMovement.objects.filter(
                    product=product
                ).order_by(
                    "created_at",
                    "pk",
                )
            )

            self.assertEqual(len(movements), 8)
            balance = 0

            for move in movements:
                self.assertEqual(
                    move.balance_before,
                    balance,
                )
                balance += (
                    move.quantity
                    if move.kind == "receive"
                    else -move.quantity
                )
                self.assertEqual(
                    move.balance_after,
                    balance,
                )
                self.assertEqual(
                    move.actor_id,
                    self.admin.pk,
                )
                self.assertLess(
                    move.created_at,
                    now,
                )

            self.assertEqual(
                StockBalance.objects.get(
                    product=product
                ).quantity,
                balance,
            )
            self.assertEqual(
                (
                    movements[-1].created_at
                    - movements[0].created_at
                ).days,
                28,
            )

    def test_repeat_does_not_duplicate_or_overwrite_changes(self):
        self.seed()

        product = Product.objects.get(
            sku="DEMO-V1-004"
        )
        product.name = "Edited Demo Product"
        product.save(update_fields=["name"])

        record_stock_movement(
            product_id=product.pk,
            actor=self.admin,
            kind="receive",
            quantity=2,
            reason="Demo interaction after seeding",
            operation_id=uuid4(),
        )

        before = self.counts()

        self.assertIn(
            "already exists",
            self.seed(),
        )
        self.assertEqual(
            self.counts(),
            before,
        )

        product.refresh_from_db()
        self.assertEqual(
            product.name,
            "Edited Demo Product",
        )
        self.assertEqual(
            StockBalance.objects.get(
                product=product
            ).quantity,
            22,
        )

    def test_product_identifier_collision_changes_nothing(self):
        product = Product.objects.create(
            name="Existing Product",
            sku="DEMO-V1-001",
        )
        Product.objects.filter(
            pk=product.pk
        ).update(sku="demo-v1-001")

        before = self.counts()

        with self.assertRaises(CommandError):
            self.seed()

        self.assertEqual(
            self.counts(),
            before,
        )

    def test_supplier_identifier_collision_changes_nothing(self):
        Supplier.objects.create(
            name="Existing Supplier",
            code="DEMO-SUP-V1-01",
        )
        before = self.counts()

        with self.assertRaises(CommandError):
            self.seed()

        self.assertEqual(
            self.counts(),
            before,
        )

    def test_invalid_or_unpermitted_actor_changes_nothing(self):
        for username in [
            "missing-user",
            self.member.username,
        ]:
            with (
                self.subTest(username=username),
                self.assertRaises(CommandError),
            ):
                self.seed(actor=username)

        self.assertEqual(
            self.counts(),
            (0, 0, 0, 0),
        )

        self.admin.is_active = False
        self.admin.save(update_fields=["is_active"])

        with self.assertRaises(CommandError):
            self.seed()

        self.assertEqual(
            self.counts(),
            (0, 0, 0, 0),
        )

    def test_different_actor_cannot_reseed_existing_dataset(self):
        self.seed()

        other = get_user_model().objects.create_superuser(
            username="other-seeder",
            email="other@example.com",
            password="Other-seeder-password-482!",
        )
        before = self.counts()

        with self.assertRaises(CommandError):
            self.seed(actor=other.username)

        self.assertEqual(
            self.counts(),
            before,
        )

    def test_failure_rolls_back_entire_dataset(self):
        calls = 0

        def failing_movement(**kwargs):
            nonlocal calls
            calls += 1

            if calls == 4:
                raise RuntimeError(
                    "Simulated demo failure"
                )

            return record_stock_movement(**kwargs)

        with (
            patch(
                "inventory.management.commands.seed_demo.record_stock_movement",
                side_effect=failing_movement,
            ),
            self.assertRaises(RuntimeError),
        ):
            self.seed()

        self.assertEqual(
            self.counts(),
            (0, 0, 0, 0),
        )
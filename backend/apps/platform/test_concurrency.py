"""Real PostgreSQL transactions; SQLite deliberately cannot validate this.
Confirms the DB-level partial unique constraint on SubscriptionPlan.is_default
actually rejects a second concurrent "make this the default plan" write, not
just the service-level check-then-update in services.create_plan/update_plan.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import IntegrityError, connection, connections
from django.test import TransactionTestCase

from .models import SubscriptionPlan
from .services import create_plan


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class DefaultPlanConstraintConcurrencyTests(TransactionTestCase):
    def setUp(self):
        # The 0002 data migration already seeded a "Full Access" default
        # plan -- unset it so this test starts from a clean slate.
        SubscriptionPlan.objects.filter(is_default=True).update(is_default=False)
        self.plan_a = SubscriptionPlan.objects.create(name="Plan A", module_codes=["finance"])
        self.plan_b = SubscriptionPlan.objects.create(name="Plan B", module_codes=["attendance"])

    def _with_bounded_connection(self, barrier, fn):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            barrier.wait(timeout=6)
            return fn()
        finally:
            connections.close_all()

    def test_two_concurrent_writes_never_leave_two_default_plans(self):
        barrier = Barrier(2)

        def make_default(plan_id):
            def attempt():
                try:
                    SubscriptionPlan.objects.filter(pk=plan_id).update(is_default=True)
                    return "ok"
                except IntegrityError:
                    return "conflict"
            return attempt

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._with_bounded_connection, barrier, make_default(self.plan_a.pk)),
                pool.submit(self._with_bounded_connection, barrier, make_default(self.plan_b.pk)),
            ]
            results = [future.result(timeout=15) for future in futures]

        self.assertIn("conflict", results)
        self.assertEqual(SubscriptionPlan.objects.filter(is_default=True).count(), 1)

    def test_concurrent_create_plan_as_default_never_raises_unhandled_and_leaves_one_default(self):
        """The service-level operation, not just the raw DB constraint:
        two admins simultaneously creating a new default plan must each
        either succeed cleanly or fail with a clear ValidationError -- never
        an unhandled IntegrityError bubbling out of create_plan.
        """
        barrier = Barrier(2)

        def make_new_default(name):
            def attempt():
                try:
                    create_plan(name=name, module_codes=["finance"], is_default=True)
                    return "ok"
                except Exception as error:
                    from django.core.exceptions import ValidationError
                    if isinstance(error, ValidationError):
                        return "rejected"
                    raise
            return attempt

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._with_bounded_connection, barrier, make_new_default("Candidate A")),
                pool.submit(self._with_bounded_connection, barrier, make_new_default("Candidate B")),
            ]
            results = [future.result(timeout=15) for future in futures]

        self.assertTrue(set(results).issubset({"ok", "rejected"}))
        self.assertEqual(SubscriptionPlan.objects.filter(is_default=True).count(), 1)

"""Real PostgreSQL transactions; SQLite deliberately cannot validate these tests."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import connection, connections
from django.test import TransactionTestCase

from apps.notifications.models import NotificationChannel, NotificationOutbox
from apps.tenancy.models import Tenant

from .durable_work import claim_due


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-locking semantics")
class ClaimDueConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")
        self.row_a = NotificationOutbox.objects.create(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0700000001",
            message_type="test", idempotency_key="race-a",
        )
        self.row_b = NotificationOutbox.objects.create(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0700000002",
            message_type="test", idempotency_key="race-b",
        )

    def claim_one(self, barrier):
        # Mirrors apps/finance/test_concurrency.py's worker pattern: each
        # thread owns its own connection, with bounded DB waits so a bug
        # that reintroduces blocking (instead of skip_locked) fails fast
        # rather than hanging.
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            barrier.wait(timeout=6)
            claimed = claim_due(NotificationOutbox.objects.filter(tenant=self.tenant), limit=1)
            return [row.pk for row in claimed]
        finally:
            connections.close_all()

    def test_two_concurrent_claims_never_double_claim_the_same_row(self):
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.claim_one, barrier) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]

        claimed_pks = [pk for result in results for pk in result]
        self.assertCountEqual(claimed_pks, [self.row_a.pk, self.row_b.pk])

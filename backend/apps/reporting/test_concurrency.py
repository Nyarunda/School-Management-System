"""Real PostgreSQL transactions; SQLite deliberately cannot validate this.
Mirrors the double-claim coverage already proven for NotificationOutbox
(apps.activity.test_durable_work_concurrency) and NotificationEvent
(apps.notifications.test_concurrency), applied to the third
DurableWorkModel subclass this codebase now has.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import connection, connections
from django.test import TransactionTestCase

from apps.activity.durable_work import claim_due
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import ReportExportJob
from .services import request_report_export


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class ReportExportJobConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")

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

    def test_two_concurrent_claims_never_double_claim_the_same_job(self):
        row_a = ReportExportJob.objects.create(tenant=self.tenant, report_code="students.enrollment_register", params={})
        row_b = ReportExportJob.objects.create(tenant=self.tenant, report_code="students.enrollment_register", params={})
        barrier = Barrier(2)

        def claim_one():
            claimed = claim_due(ReportExportJob.objects.filter(tenant=self.tenant), limit=1)
            return [row.pk for row in claimed]

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._with_bounded_connection, barrier, claim_one) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]

        claimed_pks = [pk for result in results for pk in result]
        self.assertCountEqual(claimed_pks, [row_a.pk, row_b.pk])

    def test_concurrent_export_requests_with_the_same_idempotency_key_resolve_to_one_job(self):
        admin = User.objects.create_user(username="admin", password="secret")
        role = Role.objects.create(tenant=self.tenant, name="Admin", permissions=["reports.students.export"])
        Membership.objects.create(tenant=self.tenant, user=admin, role=role)
        barrier = Barrier(2)

        def request_export():
            job = request_report_export(
                user=admin, tenant=self.tenant, report_code="students.enrollment_register", params={},
                idempotency_key="race-key",
            )
            return job.pk

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._with_bounded_connection, barrier, request_export) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]

        self.assertEqual(len(set(results)), 1)
        self.assertEqual(ReportExportJob.objects.filter(tenant=self.tenant, idempotency_key="race-key").count(), 1)

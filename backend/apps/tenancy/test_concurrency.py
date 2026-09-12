"""Real PostgreSQL transactions; SQLite deliberately cannot validate this.
Confirms _ensure_not_removing_last_administrator's select_for_update lock
actually prevents two concurrent requests from both deactivating one of the
tenant's last two administrators, which would otherwise leave the tenant
with zero active administrators (a classic check-then-act race).
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TransactionTestCase

from .models import Membership, Role, Tenant, User
from .services import deactivate_membership


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row-locking semantics")
class LastAdministratorConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.admin_role = Role.objects.create(
            tenant=self.tenant, name="Administrator", permissions=["tenancy.membership.manage"],
        )
        self.user_a = User.objects.create_user(username="admin-a", password="secret")
        self.user_b = User.objects.create_user(username="admin-b", password="secret")
        self.membership_a = Membership.objects.create(tenant=self.tenant, user=self.user_a, role=self.admin_role)
        self.membership_b = Membership.objects.create(tenant=self.tenant, user=self.user_b, role=self.admin_role)

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

    def test_two_concurrent_deactivations_of_the_last_two_administrators_never_both_succeed(self):
        barrier = Barrier(2)

        def deactivate(actor, membership):
            def attempt():
                try:
                    deactivate_membership(actor=actor, tenant=self.tenant, membership=membership)
                    return "ok"
                except ValidationError:
                    return "rejected"
            return attempt

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._with_bounded_connection, barrier, deactivate(self.user_a, self.membership_a)),
                pool.submit(self._with_bounded_connection, barrier, deactivate(self.user_b, self.membership_b)),
            ]
            results = [future.result(timeout=15) for future in futures]

        self.assertEqual(sorted(results), ["ok", "rejected"])
        self.assertEqual(
            Membership.objects.filter(tenant=self.tenant, is_active=True, role=self.admin_role).count(), 1,
        )

"""Real PostgreSQL transactions; SQLite deliberately cannot validate these tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TransactionTestCase

from apps.tenancy.models import Membership, Role, Tenant, User

from .models import Employee
from .services import create_employee


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class EmployeeConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.admin = User.objects.create_user(username="admin", password="secret")
        self.role = Role.objects.create(tenant=self.tenant, name="HR Officer", permissions=["staff.manage"])
        Membership.objects.create(tenant=self.tenant, user=self.admin, role=self.role)

    def _attempt_create(self, *, first_name, barrier):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")

            def synchronize_insert(execute, sql, params, many, context):
                if sql.startswith('INSERT INTO "staff_employee"'):
                    barrier.wait(timeout=6)
                return execute(sql, params, many, context)

            with connection.execute_wrapper(synchronize_insert):
                try:
                    create_employee(
                        user=self.admin, tenant=self.tenant, employee_number="EMP-100", first_name=first_name,
                        last_name="Doe", job_title="Teacher", employment_type="PERMANENT", hire_date=date(2024, 1, 1),
                    )
                    return "created"
                except ValidationError as error:
                    return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_competing_employee_number_creation_resolves_to_exactly_one_row(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._attempt_create, first_name="Alice", barrier=barrier),
                pool.submit(self._attempt_create, first_name="Bob", barrier=barrier),
            ]
            outcomes = [future.result(timeout=15) for future in futures]
        self.assertEqual(Employee.objects.filter(tenant=self.tenant, employee_number="EMP-100").count(), 1)
        self.assertEqual(sorted(outcomes), sorted(["created", "Employee number already in use for this tenant"]))

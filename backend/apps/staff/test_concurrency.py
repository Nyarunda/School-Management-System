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
from .services import change_employment_status, create_employee, link_user_account


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class EmployeeConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.admin = User.objects.create_user(username="admin", password="secret")
        self.role = Role.objects.create(tenant=self.tenant, name="HR Officer", permissions=["staff.manage", "staff.user_link.manage"])
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

    def _attempt_status_change(self, *, employee_id, status, barrier):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            barrier.wait(timeout=6)
            employee = Employee.objects.get(pk=employee_id)
            try:
                change_employment_status(user=self.admin, tenant=self.tenant, employee=employee, status=status)
                return "changed"
            except ValidationError as error:
                return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_concurrent_status_transitions_to_a_terminal_state_serialize(self):
        employee = create_employee(
            user=self.admin, tenant=self.tenant, employee_number="EMP-200", first_name="Carl",
            last_name="Doe", job_title="Teacher", employment_type="PERMANENT", hire_date=date(2024, 1, 1),
        )
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._attempt_status_change, employee_id=employee.id, status="TERMINATED", barrier=barrier),
                pool.submit(self._attempt_status_change, employee_id=employee.id, status="TERMINATED", barrier=barrier),
            ]
            outcomes = [future.result(timeout=15) for future in futures]
        employee.refresh_from_db()
        self.assertEqual(employee.status, "TERMINATED")
        self.assertEqual(sorted(outcomes), sorted(["changed", "Cannot move employee from TERMINATED to TERMINATED"]))

    def _attempt_link(self, *, employee_id, user_account_id, barrier):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")

            def synchronize_update(execute, sql, params, many, context):
                if sql.startswith('UPDATE "staff_employee"'):
                    barrier.wait(timeout=6)
                return execute(sql, params, many, context)

            with connection.execute_wrapper(synchronize_update):
                employee = Employee.objects.get(pk=employee_id)
                user_account = User.objects.get(pk=user_account_id)
                try:
                    link_user_account(user=self.admin, tenant=self.tenant, employee=employee, user_account=user_account)
                    return "linked"
                except ValidationError as error:
                    return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_competing_links_to_the_same_user_resolve_to_exactly_one_employee(self):
        employee_one = create_employee(
            user=self.admin, tenant=self.tenant, employee_number="EMP-300", first_name="A", last_name="One",
            job_title="Teacher", employment_type="PERMANENT", hire_date=date(2024, 1, 1),
        )
        employee_two = create_employee(
            user=self.admin, tenant=self.tenant, employee_number="EMP-301", first_name="B", last_name="Two",
            job_title="Teacher", employment_type="PERMANENT", hire_date=date(2024, 1, 1),
        )
        shared_user = User.objects.create_user(username="shared", password="secret")
        Membership.objects.create(tenant=self.tenant, user=shared_user, role=self.role)

        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._attempt_link, employee_id=employee_one.id, user_account_id=shared_user.id, barrier=barrier),
                pool.submit(self._attempt_link, employee_id=employee_two.id, user_account_id=shared_user.id, barrier=barrier),
            ]
            outcomes = [future.result(timeout=15) for future in futures]
        self.assertEqual(Employee.objects.filter(tenant=self.tenant, user_account=shared_user).count(), 1)
        self.assertEqual(sorted(outcomes), sorted(["linked", "This user account is already linked to another employee record"]))

"""Real PostgreSQL transactions; SQLite deliberately cannot validate these tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TransactionTestCase

from apps.staff.services import create_employee
from apps.tenancy.models import Campus, Membership, Role, Tenant, User

from .models import LeaveLedgerEntry, LeaveLedgerEntryType, LeaveRequest, LeaveRequestApprovalStatus, LeaveRequestStatus
from .services import (
    add_workflow_stage,
    cancel_approved_leave_request,
    create_leave_type,
    create_leave_workflow,
    decide_leave_request_stage,
    grant_leave_entitlement,
)
from .services import create_leave_request as create_leave_request_service
from .services import submit_leave_request as submit_leave_request_service


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class LeaveConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.admin = User.objects.create_user(username="admin", password="secret")
        self.admin_role = Role.objects.create(
            tenant=self.tenant, name="HR Admin",
            permissions=["staff.manage", "leave.setup.manage", "leave.request.manage", "leave.balance.adjust"],
        )
        Membership.objects.create(tenant=self.tenant, user=self.admin, role=self.admin_role)

        self.supervisor_role = Role.objects.create(tenant=self.tenant, name="Supervisor", permissions=["leave.approve"])
        self.supervisor = User.objects.create_user(username="supervisor", password="secret")
        Membership.objects.create(tenant=self.tenant, user=self.supervisor, role=self.supervisor_role)

        Campus.objects.create(tenant=self.tenant, name="Main", code="MAIN")

        self.employee = create_employee(
            user=self.admin, tenant=self.tenant, employee_number="EMP-001", first_name="Jane", last_name="Doe",
            job_title="Teacher", employment_type="PERMANENT", hire_date=date(2020, 1, 1),
        )

        self.workflow = create_leave_workflow(user=self.admin, tenant=self.tenant, name="Standard")
        add_workflow_stage(
            user=self.admin, tenant=self.tenant, workflow=self.workflow, sequence=1, name="Supervisor",
            approver_role=self.supervisor_role,
        )
        self.leave_type = create_leave_type(
            user=self.admin, tenant=self.tenant, name="Annual Leave", code="ANNUAL",
            default_annual_entitlement_days=21, approval_workflow=self.workflow,
        )
        grant_leave_entitlement(user=self.admin, tenant=self.tenant, employee=self.employee, leave_type=self.leave_type, year=2026, days=6)

    def _submit(self, *, start_date, end_date):
        request = create_leave_request_service(
            user=self.admin, tenant=self.tenant, employee=self.employee, leave_type=self.leave_type,
            start_date=start_date, end_date=end_date,
        )
        return submit_leave_request_service(user=self.admin, tenant=self.tenant, leave_request=request)

    def _attempt_final_approval(self, *, request_id, barrier):
        # Both requests belong to the same employee; the natural serialization
        # point is decide_leave_request_stage's own Employee.select_for_update()
        # lock (a SELECT, not an UPDATE) -- so this just rendezvous both threads
        # right before they call it, mirroring apps/timetable/test_concurrency.py's
        # _attempt_update, rather than intercepting a specific SQL statement.
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            barrier.wait(timeout=6)
            leave_request = LeaveRequest.objects.get(pk=request_id)
            try:
                decide_leave_request_stage(
                    user=self.supervisor, tenant=self.tenant, leave_request=leave_request,
                    decision=LeaveRequestApprovalStatus.APPROVED,
                )
                return "approved"
            except ValidationError as error:
                return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_competing_final_approvals_never_consume_past_the_available_balance(self):
        # Employee has 6 days available; each request asks for 5 (Mon-Fri).
        request_one = self._submit(start_date=date(2026, 1, 5), end_date=date(2026, 1, 9))
        request_two = self._submit(start_date=date(2026, 2, 2), end_date=date(2026, 2, 6))

        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._attempt_final_approval, request_id=request_one.id, barrier=barrier),
                pool.submit(self._attempt_final_approval, request_id=request_two.id, barrier=barrier),
            ]
            outcomes = [future.result(timeout=15) for future in futures]

        approved_count = LeaveRequest.objects.filter(pk__in=[request_one.id, request_two.id], status=LeaveRequestStatus.APPROVED).count()
        self.assertEqual(approved_count, 1)
        self.assertEqual(sorted(outcomes), sorted(["approved", "Insufficient leave balance to approve this request"]))
        consumed_total = sum(
            e.days for e in LeaveLedgerEntry.objects.filter(tenant=self.tenant, employee=self.employee, leave_type=self.leave_type, leave_year=2026)
        )
        self.assertEqual(consumed_total, 1)  # 6 granted - 5 consumed by exactly one request

    def _attempt_decision(self, *, request_id, barrier):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            barrier.wait(timeout=6)
            leave_request = LeaveRequest.objects.get(pk=request_id)
            try:
                decide_leave_request_stage(
                    user=self.supervisor, tenant=self.tenant, leave_request=leave_request,
                    decision=LeaveRequestApprovalStatus.APPROVED,
                )
                return "approved"
            except ValidationError as error:
                return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_concurrent_decisions_on_the_same_stage_serialize(self):
        request = self._submit(start_date=date(2026, 3, 2), end_date=date(2026, 3, 3))

        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._attempt_decision, request_id=request.id, barrier=barrier),
                pool.submit(self._attempt_decision, request_id=request.id, barrier=barrier),
            ]
            outcomes = [future.result(timeout=15) for future in futures]

        request.refresh_from_db()
        self.assertEqual(request.status, LeaveRequestStatus.APPROVED)
        self.assertEqual(sorted(outcomes), sorted(["approved", "Only submitted requests can be decided"]))

    def _attempt_cancellation(self, *, request_id, barrier):
        # RC Area 3 verification gap: cancel_approved_leave_request already
        # locks the LeaveRequest (and, when balance-bearing, the Employee)
        # row -- this proves that locking actually serializes two
        # concurrent cancellations of the same approved request, mirroring
        # the same-stage-decision race above.
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            barrier.wait(timeout=6)
            leave_request = LeaveRequest.objects.get(pk=request_id)
            try:
                cancel_approved_leave_request(user=self.admin, tenant=self.tenant, leave_request=leave_request)
                return "cancelled"
            except ValidationError as error:
                return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_competing_cancellations_of_the_same_request_serialize_and_reverse_exactly_once(self):
        request = self._submit(start_date=date(2026, 4, 6), end_date=date(2026, 4, 10))
        decide_leave_request_stage(
            user=self.supervisor, tenant=self.tenant, leave_request=request, decision=LeaveRequestApprovalStatus.APPROVED,
        )

        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._attempt_cancellation, request_id=request.id, barrier=barrier),
                pool.submit(self._attempt_cancellation, request_id=request.id, barrier=barrier),
            ]
            outcomes = [future.result(timeout=15) for future in futures]

        request.refresh_from_db()
        self.assertEqual(request.status, LeaveRequestStatus.CANCELLED)
        self.assertEqual(sorted(outcomes), sorted(["cancelled", "Only approved requests can be cancelled"]))
        reversals = LeaveLedgerEntry.objects.filter(
            tenant=self.tenant, employee=self.employee, leave_type=self.leave_type, entry_type=LeaveLedgerEntryType.REVERSAL,
        )
        self.assertEqual(reversals.count(), 1)
        self.assertEqual(reversals.first().days, request.requested_days)

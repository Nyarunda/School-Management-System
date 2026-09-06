"""Real PostgreSQL transactions; SQLite deliberately cannot validate these tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from apps.academics.models import AcademicLevel, AcademicYear
from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import CreditNote, CreditNoteStatus, NumberSeries, Payment, PaymentAllocation, PaymentMethod, Receipt
from .selectors import student_balance
from .services import (
    add_fee_structure_line,
    allocate_payment,
    approve_fee_structure,
    assign_fee_structure,
    create_fee_structure,
    generate_invoice,
    issue_credit_note,
    issue_invoice,
    record_payment,
)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class PaymentAllocationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.user = User.objects.create_user(username="bursar", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a,
            name="Bursar",
            permissions=[
                "finance.fee_structure.create",
                "finance.fee_structure.edit",
                "finance.fee_structure.approve",
                "finance.invoice.create",
                "finance.invoice.issue",
                "finance.credit_note.create",
                "finance.payment.record",
                "finance.payment.allocate",
            ],
        )
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role)
        year = AcademicYear.objects.create(tenant=self.school_a, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        level = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.payment_method = PaymentMethod.objects.create(tenant=self.school_a, name="Bank transfer", code="BANK")
        NumberSeries.objects.create(tenant=self.school_a, document_type="INVOICE", prefix="INV-2026-", padding=6)
        NumberSeries.objects.create(tenant=self.school_a, document_type="RECEIPT", prefix="RCT-2026-", padding=6)
        NumberSeries.objects.create(tenant=self.school_a, document_type="CREDIT_NOTE", prefix="CRN-2026-", padding=6)
        self.structure = create_fee_structure(user=self.user, tenant=self.school_a, name="Grade 8 2026", academic_year=year, academic_level=level)

        from .models import FeeCategory, FeeItem

        category = FeeCategory.objects.create(tenant=self.school_a, name="Tuition", code="TUITION")
        item = FeeItem.objects.create(tenant=self.school_a, category=category, name="Tuition fee", code="TUITION")
        add_fee_structure_line(user=self.user, tenant=self.school_a, fee_structure=self.structure, fee_item=item, amount=Decimal("50000.00"))
        approve_fee_structure(user=self.user, tenant=self.school_a, fee_structure=self.structure)

        self.student = Student.objects.create(tenant=self.school_a, admission_number="ADM-001", first_name="Amina", last_name="Otieno")

    def _issued_invoice(self, student, structure=None):
        # A student can only have one invoice per fee structure (StudentFeeAssignment
        # is unique per tenant/student/fee_structure), so a test needing two distinct
        # invoices for the same student must use two distinct structures.
        assignment = assign_fee_structure(user=self.user, tenant=self.school_a, student=student, fee_structure=structure or self.structure)
        invoice = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)
        return issue_invoice(user=self.user, tenant=self.school_a, invoice=invoice)

    def _new_structure(self, name):
        from .models import FeeCategory, FeeItem

        year = self.structure.academic_year
        level = self.structure.academic_level
        structure = create_fee_structure(user=self.user, tenant=self.school_a, name=name, academic_year=year, academic_level=level)
        category = FeeCategory.objects.create(tenant=self.school_a, name=name, code=name.upper().replace(" ", "_"))
        item = FeeItem.objects.create(tenant=self.school_a, category=category, name=name, code=category.code)
        add_fee_structure_line(user=self.user, tenant=self.school_a, fee_structure=structure, fee_item=item, amount=Decimal("50000.00"))
        approve_fee_structure(user=self.user, tenant=self.school_a, fee_structure=structure)
        return structure

    def attempt(self, payment, invoice, barrier=None):
        # Each worker owns its connection and transaction, with bounded DB waits.
        # allocate_payment serializes competing allocations with select_for_update
        # (a real row lock), not a bare unique constraint at insert time, so the
        # two threads must not be synchronized *inside* that locked section --
        # whichever thread loses the race blocks on the lock itself until the
        # winner commits, then correctly sees the updated sum and is rejected.
        # The barrier only lines up the start so both attempts genuinely race.
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            if barrier is not None:
                barrier.wait(timeout=6)
            try:
                allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=invoice, amount=Decimal("50000.00"))
                return "created"
            except ValidationError as error:
                return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_competing_allocations_from_the_same_payment_create_exactly_one(self):
        payment = record_payment(
            user=self.user, tenant=self.school_a, student=self.student, payment_method=self.payment_method,
            amount=Decimal("50000.00"), idempotency_key="pay-race-1",
        )
        invoice_a = self._issued_invoice(self.student, self.structure)
        invoice_b = self._issued_invoice(self.student, self._new_structure("Transport"))
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.attempt, payment, invoice, barrier) for invoice in (invoice_a, invoice_b)]
            outcomes = [future.result(timeout=15) for future in futures]

        self.assertCountEqual(outcomes, ["created", "Allocation exceeds the payment's unallocated amount"])
        self.assertEqual(PaymentAllocation.objects.filter(payment=payment).count(), 1)

    def test_competing_allocations_to_the_same_invoice_create_exactly_one(self):
        invoice = self._issued_invoice(self.student)
        payment_a = record_payment(
            user=self.user, tenant=self.school_a, student=self.student, payment_method=self.payment_method,
            amount=Decimal("50000.00"), idempotency_key="pay-race-a",
        )
        payment_b = record_payment(
            user=self.user, tenant=self.school_a, student=self.student, payment_method=self.payment_method,
            amount=Decimal("50000.00"), idempotency_key="pay-race-b",
        )
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.attempt, payment, invoice, barrier) for payment in (payment_a, payment_b)]
            outcomes = [future.result(timeout=15) for future in futures]

        self.assertCountEqual(outcomes, ["created", "Allocation exceeds the invoice's outstanding balance"])
        self.assertEqual(PaymentAllocation.objects.filter(invoice=invoice).count(), 1)

    def test_foreign_tenant_payment_is_rejected_without_waiting_for_its_lock(self):
        # allocate_payment's validate_same_tenant() check compares in-memory
        # tenant_id fields and never touches the database, so a cross-tenant
        # payment is rejected before the tenant-scoped select_for_update()
        # is even attempted -- it cannot queue behind the held lock below.
        foreign_student = Student.objects.create(tenant=self.school_b, admission_number="FOREIGN", first_name="Foreign", last_name="Student")
        foreign_method = PaymentMethod.objects.create(tenant=self.school_b, name="Cash", code="CASH")
        foreign_payment = Payment.objects.create(
            tenant=self.school_b, student=foreign_student, payment_method=foreign_method,
            amount=Decimal("1000.00"), idempotency_key="foreign-pay",
        )
        invoice = self._issued_invoice(self.student)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Payment.objects.select_for_update().get(pk=foreign_payment.pk)
                future = pool.submit(self.attempt, foreign_payment, invoice)
                self.assertIn("must belong to the same tenant", future.result(timeout=3))
        self.assertEqual(PaymentAllocation.objects.count(), 0)

    def record_attempt(self, barrier=None):
        # record_payment catches IntegrityError on a duplicate idempotency key
        # and re-queries within the same outer transaction to replay the
        # existing Payment. On PostgreSQL that replay query only works because
        # the risky insert is wrapped in its own nested atomic() (a savepoint);
        # without it, the whole transaction would already be aborted.
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            if barrier is not None:
                barrier.wait(timeout=6)
            payment = record_payment(
                user=self.user, tenant=self.school_a, student=self.student, payment_method=self.payment_method,
                amount=Decimal("50000.00"), idempotency_key="pay-duplicate-submit",
            )
            return str(payment.id)
        finally:
            connections.close_all()

    def test_competing_payments_with_the_same_idempotency_key_replay_to_one(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.record_attempt, barrier) for _ in range(2)]
            payment_ids = [future.result(timeout=15) for future in futures]

        self.assertEqual(payment_ids[0], payment_ids[1])
        self.assertEqual(Payment.objects.filter(idempotency_key="pay-duplicate-submit").count(), 1)
        self.assertEqual(Receipt.objects.filter(payment_id=payment_ids[0]).count(), 1)

    def credit_note_attempt(self, invoice, amount, barrier=None):
        # issue_credit_note locks the same Invoice row as allocate_payment,
        # via the identical tenant-scoped select_for_update().get() idiom.
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            if barrier is not None:
                barrier.wait(timeout=6)
            try:
                issue_credit_note(user=self.user, tenant=self.school_a, student=self.student, invoice=invoice, amount=amount, reason="Bursary")
                return "created"
            except ValidationError as error:
                return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_competing_credit_notes_cannot_overcredit_invoice(self):
        invoice = self._issued_invoice(self.student)
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.credit_note_attempt, invoice, Decimal("50000.00"), barrier) for _ in range(2)]
            outcomes = [future.result(timeout=15) for future in futures]

        self.assertCountEqual(outcomes, ["created", "Credit note exceeds the invoice balance"])
        self.assertEqual(CreditNote.objects.filter(invoice=invoice, status=CreditNoteStatus.ISSUED).count(), 1)

    def test_mixed_credit_note_and_payment_allocation_both_lock_the_same_invoice_safely(self):
        # A credit note bounds only against the invoice's face value minus
        # prior credits; a payment allocation bounds against face value minus
        # credits *and* prior payments. This asymmetry is intentional -- a
        # credit note is a formal write-down of what's owed and can land
        # regardless of payment history (e.g. a fee waiver issued as a refund
        # after full payment), while a payment must respect what's actually
        # still owed, credits included. So which one "wins" a race is
        # legitimately order-dependent: whichever locks the invoice first
        # commits first, and the second sees its fully caught-up state. What
        # this test actually proves is that the two *different* service
        # functions locking the *same* invoice row don't deadlock each other
        # and each decision is made against consistent, non-stale data --
        # not that a fixed ordering always wins.
        invoice = self._issued_invoice(self.student)
        payment = record_payment(
            user=self.user, tenant=self.school_a, student=self.student, payment_method=self.payment_method,
            amount=Decimal("50000.00"), idempotency_key="pay-mixed",
        )
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            credit_future = pool.submit(self.credit_note_attempt, invoice, Decimal("50000.00"), barrier)
            allocate_future = pool.submit(self.attempt, payment, invoice, barrier)
            credit_outcome, allocate_outcome = credit_future.result(timeout=15), allocate_future.result(timeout=15)

        # The credit note is never bounded by payments, so it always lands.
        self.assertEqual(credit_outcome, "created")
        self.assertEqual(CreditNote.objects.filter(invoice=invoice, status=CreditNoteStatus.ISSUED).count(), 1)

        if allocate_outcome == "created":
            self.assertEqual(PaymentAllocation.objects.filter(invoice=invoice).count(), 1)
            self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("-50000.00"))
        else:
            self.assertEqual(allocate_outcome, "Allocation exceeds the invoice's outstanding balance")
            self.assertEqual(PaymentAllocation.objects.filter(invoice=invoice).count(), 0)
            self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("0.00"))

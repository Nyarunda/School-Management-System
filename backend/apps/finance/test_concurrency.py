"""Real PostgreSQL transactions; SQLite deliberately cannot validate these tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.db.models import Sum
from django.test import TransactionTestCase

from apps.academics.models import AcademicLevel, AcademicYear, Term
from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import (
    AllocationReversal,
    CreditNote,
    CreditNoteStatus,
    IncomingPayment,
    NumberSeries,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    PaymentStatus,
    ReconciliationStatus,
    Receipt,
    StudentFeeAssignment,
)
from .selectors import student_balance
from .services import (
    add_fee_structure_line,
    allocate_payment,
    approve_fee_structure,
    assign_fee_structure,
    create_fee_structure,
    generate_invoice,
    ignore_incoming_payment,
    ingest_incoming_payment,
    issue_credit_note,
    issue_invoice,
    match_incoming_payment,
    record_payment,
    reverse_payment,
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
                "finance.payment.reverse",
            ],
        )
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role)
        year = AcademicYear.objects.create(tenant=self.school_a, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        level = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        term = Term.objects.create(tenant=self.school_a, academic_year=year, name="Term 1", starts_on=date(2026, 1, 1), ends_on=date(2026, 4, 30), sequence=1)
        self.payment_method = PaymentMethod.objects.create(tenant=self.school_a, name="Bank transfer", code="BANK")
        NumberSeries.objects.create(tenant=self.school_a, document_type="INVOICE", prefix="INV-2026-", padding=6)
        NumberSeries.objects.create(tenant=self.school_a, document_type="RECEIPT", prefix="RCT-2026-", padding=6)
        NumberSeries.objects.create(tenant=self.school_a, document_type="CREDIT_NOTE", prefix="CRN-2026-", padding=6)
        NumberSeries.objects.create(tenant=self.school_a, document_type="PAYMENT_REVERSAL", prefix="PRV-2026-", padding=6)
        self.structure = create_fee_structure(user=self.user, tenant=self.school_a, name="Grade 8 2026", academic_year=year, academic_level=level, term=term)

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
        term = self.structure.term
        structure = create_fee_structure(user=self.user, tenant=self.school_a, name=name, academic_year=year, academic_level=level, term=term)
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

    def reverse_payment_attempt(self, payment, barrier=None):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            if barrier is not None:
                barrier.wait(timeout=6)
            try:
                reverse_payment(user=self.user, tenant=self.school_a, payment=payment, reason="Bounced cheque")
                return "reversed"
            except ValidationError as error:
                return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_concurrent_whole_payment_reversals_sharing_invoices_do_not_deadlock(self):
        # reverse_payment() is the first path that locks more than one
        # invoice in a single transaction, so this is the one scenario where
        # a lock-ordering deadlock is actually possible: two concurrent
        # reversals whose allocations touch the same two invoices, in
        # opposite natural order. The ascending-invoice_id lock order in
        # reverse_payment() must prevent that regardless of thread timing.
        invoice_x = self._issued_invoice(self.student, self.structure)
        invoice_y = self._issued_invoice(self.student, self._new_structure("Transport"))
        payment_1 = record_payment(
            user=self.user, tenant=self.school_a, student=self.student, payment_method=self.payment_method,
            amount=Decimal("50000.00"), idempotency_key="pay-multi-1",
        )
        payment_2 = record_payment(
            user=self.user, tenant=self.school_a, student=self.student, payment_method=self.payment_method,
            amount=Decimal("50000.00"), idempotency_key="pay-multi-2",
        )
        allocate_payment(user=self.user, tenant=self.school_a, payment=payment_1, invoice=invoice_x, amount=Decimal("25000.00"))
        allocate_payment(user=self.user, tenant=self.school_a, payment=payment_1, invoice=invoice_y, amount=Decimal("25000.00"))
        allocate_payment(user=self.user, tenant=self.school_a, payment=payment_2, invoice=invoice_x, amount=Decimal("25000.00"))
        allocate_payment(user=self.user, tenant=self.school_a, payment=payment_2, invoice=invoice_y, amount=Decimal("25000.00"))
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("0.00"))
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.reverse_payment_attempt, payment, barrier) for payment in (payment_1, payment_2)]
            outcomes = [future.result(timeout=15) for future in futures]

        self.assertEqual(outcomes, ["reversed", "reversed"])
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("100000.00"))

    def test_concurrent_reverse_payment_and_allocate_leave_no_active_allocation(self):
        invoice = self._issued_invoice(self.student)
        payment = record_payment(
            user=self.user, tenant=self.school_a, student=self.student, payment_method=self.payment_method,
            amount=Decimal("50000.00"), idempotency_key="pay-race-reverse",
        )
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            reverse_future = pool.submit(self.reverse_payment_attempt, payment, barrier)
            allocate_future = pool.submit(self.attempt, payment, invoice, barrier)
            reverse_outcome, allocate_outcome = reverse_future.result(timeout=15), allocate_future.result(timeout=15)

        self.assertEqual(reverse_outcome, "reversed")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.REVERSED)
        self.assertIn(allocate_outcome, ("created", "Cannot allocate a reversed payment"))
        # Whether allocate_payment lost the race outright, or briefly won
        # before being caught by the reversal's own cascade, no allocation
        # survives active (unreversed) once the dust settles.
        net_allocated = PaymentAllocation.objects.filter(payment=payment).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        net_reversed = AllocationReversal.objects.filter(allocation__payment=payment).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        self.assertEqual(net_allocated - net_reversed, Decimal("0.00"))


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class FeeAssignmentConcurrencyTests(TransactionTestCase):
    """RC Area 4 verification gap: assign_fee_structure has no
    @transaction.atomic/select_for_update of its own, relying entirely on
    Django's get_or_create (one internal retry on IntegrityError, then a
    get()). This proves two concurrent assignment attempts for the same
    (student, fee_structure) pair collapse to exactly one row without an
    unhandled IntegrityError escaping as a 500.
    """

    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.user = User.objects.create_user(username="bursar", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a,
            name="Bursar",
            permissions=["finance.fee_structure.create", "finance.fee_structure.edit", "finance.fee_structure.approve", "finance.invoice.create"],
        )
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role)
        year = AcademicYear.objects.create(tenant=self.school_a, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        level = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        term = Term.objects.create(tenant=self.school_a, academic_year=year, name="Term 1", starts_on=date(2026, 1, 1), ends_on=date(2026, 4, 30), sequence=1)
        self.structure = create_fee_structure(user=self.user, tenant=self.school_a, name="Grade 8 2026", academic_year=year, academic_level=level, term=term)

        from .models import FeeCategory, FeeItem

        category = FeeCategory.objects.create(tenant=self.school_a, name="Tuition", code="TUITION")
        item = FeeItem.objects.create(tenant=self.school_a, category=category, name="Tuition fee", code="TUITION")
        add_fee_structure_line(user=self.user, tenant=self.school_a, fee_structure=self.structure, fee_item=item, amount=Decimal("50000.00"))
        approve_fee_structure(user=self.user, tenant=self.school_a, fee_structure=self.structure)

        self.student = Student.objects.create(tenant=self.school_a, admission_number="ADM-001", first_name="Amina", last_name="Otieno")

    def attempt(self, barrier):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            barrier.wait(timeout=6)
            assignment = assign_fee_structure(user=self.user, tenant=self.school_a, student=self.student, fee_structure=self.structure)
            return str(assignment.id)
        finally:
            connections.close_all()

    def test_competing_assignments_of_the_same_student_and_structure_collapse_to_one(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.attempt, barrier) for _ in range(2)]
            outcomes = [future.result(timeout=15) for future in futures]

        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(StudentFeeAssignment.objects.filter(tenant=self.school_a, student=self.student, fee_structure=self.structure).count(), 1)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class ReconciliationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.user = User.objects.create_user(username="bursar", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a,
            name="Bursar",
            permissions=[
                "finance.payment.record",
                "finance.reconciliation.ingest",
                "finance.reconciliation.match",
                "finance.reconciliation.ignore",
            ],
        )
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role)
        self.payment_method = PaymentMethod.objects.create(tenant=self.school_a, name="Bank transfer", code="BANK")
        NumberSeries.objects.create(tenant=self.school_a, document_type="RECEIPT", prefix="RCT-2026-", padding=6)
        self.student = Student.objects.create(tenant=self.school_a, admission_number="ADM-001", first_name="Amina", last_name="Otieno")
        self.other_student = Student.objects.create(tenant=self.school_a, admission_number="ADM-002", first_name="Brian", last_name="Kiptoo")

    def _ingest(self, transaction_id):
        return ingest_incoming_payment(
            user=self.user, tenant=self.school_a, payment_method=self.payment_method,
            amount=Decimal("1000.00"), external_reference="", external_transaction_id=transaction_id,
        )

    def ingest_attempt(self, transaction_id, barrier=None):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            if barrier is not None:
                barrier.wait(timeout=6)
            incoming = self._ingest(transaction_id)
            return str(incoming.id)
        finally:
            connections.close_all()

    def match_attempt(self, incoming, student, barrier=None):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            if barrier is not None:
                barrier.wait(timeout=6)
            try:
                match_incoming_payment(user=self.user, tenant=self.school_a, incoming=incoming, student=student)
                return "matched"
            except ValidationError as error:
                return " ".join(error.messages)
        finally:
            connections.close_all()

    def ignore_attempt(self, incoming, barrier=None):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            if barrier is not None:
                barrier.wait(timeout=6)
            try:
                ignore_incoming_payment(user=self.user, tenant=self.school_a, incoming=incoming, reason="Bank fee")
                return "ignored"
            except ValidationError as error:
                return " ".join(error.messages)
        finally:
            connections.close_all()

    def test_competing_ingestion_with_the_same_transaction_id_replays_to_one(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.ingest_attempt, "bank-race-1", barrier) for _ in range(2)]
            incoming_ids = [future.result(timeout=15) for future in futures]

        self.assertEqual(incoming_ids[0], incoming_ids[1])
        self.assertEqual(IncomingPayment.objects.filter(tenant=self.school_a, external_transaction_id="bank-race-1").count(), 1)

    def test_concurrent_match_and_ignore_exactly_one_wins(self):
        incoming = self._ingest("bank-race-2")
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            match_future = pool.submit(self.match_attempt, incoming, self.student, barrier)
            ignore_future = pool.submit(self.ignore_attempt, incoming, barrier)
            outcomes = [match_future.result(timeout=15), ignore_future.result(timeout=15)]

        self.assertEqual(sum(1 for outcome in outcomes if outcome in ("matched", "ignored")), 1)
        self.assertEqual(sum(1 for outcome in outcomes if outcome == "Incoming payment has already been resolved"), 1)
        incoming.refresh_from_db()
        if incoming.status == ReconciliationStatus.MATCHED:
            self.assertIsNotNone(incoming.matched_payment)
        else:
            self.assertEqual(incoming.status, ReconciliationStatus.IGNORED)
            self.assertIsNone(incoming.matched_payment)

    def test_concurrent_manual_matches_create_at_most_one_payment(self):
        incoming = self._ingest("bank-race-3")
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.match_attempt, incoming, student, barrier)
                for student in (self.student, self.other_student)
            ]
            outcomes = [future.result(timeout=15) for future in futures]

        self.assertCountEqual(outcomes, ["matched", "Incoming payment has already been resolved"])
        self.assertEqual(Payment.objects.filter(idempotency_key=f"incoming-payment:{incoming.id}").count(), 1)

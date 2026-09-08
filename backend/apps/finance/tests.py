from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from apps.academics.models import AcademicLevel, AcademicYear
from apps.guardians.models import Guardian, StudentGuardian
from apps.notifications.models import NotificationChannel, NotificationEvent, NotificationOutbox, NotificationRecipientType
from apps.notifications.services import create_notification_rule, create_notification_template, expand_notification_event, set_channel_enabled
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import (
    FeeCategory,
    FeeItem,
    FinanceSetup,
    IncomingPayment,
    Invoice,
    NumberSeries,
    Payment,
    PaymentMethod,
    PaymentReversal,
    PaymentStatus,
    ReconciliationStatus,
    Receipt,
)
from .selectors import collections_summary_rows, fee_statement_rows, student_balance
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
    reverse_allocation,
    reverse_payment,
)
from apps.students.models import Student


class FinanceSetupTests(TestCase):
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
            ],
        )
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role)
        self.year_a = AcademicYear.objects.create(
            tenant=self.school_a,
            name="2026",
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )
        self.level_a = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        self.category_a = FeeCategory.objects.create(tenant=self.school_a, name="Tuition", code="TUITION")
        self.item_a = FeeItem.objects.create(
            tenant=self.school_a,
            category=self.category_a,
            name="Tuition fee",
            code="TUITION",
        )
        self.student_a = Student.objects.create(
            tenant=self.school_a,
            admission_number="ADM-001",
            first_name="Amina",
            last_name="Otieno",
        )
        NumberSeries.objects.create(tenant=self.school_a, document_type="INVOICE", prefix="INV-2026-", padding=6)
        NumberSeries.objects.create(tenant=self.school_a, document_type="CREDIT_NOTE", prefix="CRN-2026-", padding=6)

    def test_finance_setup_and_number_series_are_tenant_configurable(self):
        setup = FinanceSetup.objects.create(tenant=self.school_a, currency="KES")
        series = NumberSeries.objects.get(tenant=self.school_a, document_type="INVOICE")

        self.assertEqual(setup.currency, "KES")
        self.assertEqual(series.preview(), "INV-2026-000001")

    def test_fee_structure_requires_permission_and_same_tenant_setup(self):
        structure = create_fee_structure(
            user=self.user,
            tenant=self.school_a,
            name="Grade 8 2026",
            academic_year=self.year_a,
            academic_level=self.level_a,
        )

        self.assertEqual(structure.tenant, self.school_a)
        other_year = AcademicYear.objects.create(
            tenant=self.school_b,
            name="2026",
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
        )
        with self.assertRaises(ValidationError):
            create_fee_structure(
                user=self.user,
                tenant=self.school_a,
                name="Cross-tenant structure",
                academic_year=other_year,
                academic_level=self.level_a,
            )

    def test_fee_structure_must_have_lines_before_approval_and_is_immutable_after(self):
        structure = create_fee_structure(
            user=self.user,
            tenant=self.school_a,
            name="Grade 8 2026",
            academic_year=self.year_a,
            academic_level=self.level_a,
        )
        with self.assertRaises(ValidationError):
            approve_fee_structure(user=self.user, tenant=self.school_a, fee_structure=structure)

        line = add_fee_structure_line(
            user=self.user,
            tenant=self.school_a,
            fee_structure=structure,
            fee_item=self.item_a,
            amount=Decimal("25000.00"),
        )
        approve_fee_structure(user=self.user, tenant=self.school_a, fee_structure=structure)
        structure.refresh_from_db()
        self.assertTrue(structure.is_approved)

        with self.assertRaises(ValidationError):
            add_fee_structure_line(
                user=self.user,
                tenant=self.school_a,
                fee_structure=structure,
                fee_item=self.item_a,
                amount=Decimal("1000.00"),
            )
        self.assertEqual(line.amount, Decimal("25000.00"))

    def test_missing_permission_is_rejected(self):
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])

        with self.assertRaises(ValidationError):
            create_fee_structure(
                user=self.user,
                tenant=self.school_a,
                name="Unauthorized",
                academic_year=self.year_a,
                academic_level=self.level_a,
            )

    def _approved_structure(self):
        structure = create_fee_structure(
            user=self.user,
            tenant=self.school_a,
            name="Grade 8 2026",
            academic_year=self.year_a,
            academic_level=self.level_a,
        )
        add_fee_structure_line(
            user=self.user,
            tenant=self.school_a,
            fee_structure=structure,
            fee_item=self.item_a,
            amount=Decimal("50000.00"),
        )
        approve_fee_structure(user=self.user, tenant=self.school_a, fee_structure=structure)
        return structure

    def test_invoice_lines_snapshot_setup_amounts(self):
        structure = self._approved_structure()
        assignment = assign_fee_structure(
            user=self.user,
            tenant=self.school_a,
            student=self.student_a,
            fee_structure=structure,
        )
        invoice = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)

        structure.lines.update(amount=Decimal("28000.00"))
        line = invoice.lines.get()
        self.assertEqual(line.unit_amount, Decimal("50000.00"))
        self.assertEqual(invoice.total, Decimal("50000.00"))

    def test_invoice_generation_is_idempotent(self):
        structure = self._approved_structure()
        assignment = assign_fee_structure(
            user=self.user,
            tenant=self.school_a,
            student=self.student_a,
            fee_structure=structure,
        )

        first = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)
        second = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)

        self.assertEqual(first.id, second.id)
        self.assertEqual(Invoice.objects.filter(assignment=assignment).count(), 1)

    def test_issue_and_credit_note_update_calculated_student_balance(self):
        structure = self._approved_structure()
        assignment = assign_fee_structure(
            user=self.user,
            tenant=self.school_a,
            student=self.student_a,
            fee_structure=structure,
        )
        invoice = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)
        issue_invoice(user=self.user, tenant=self.school_a, invoice=invoice)
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student_a), Decimal("50000.00"))

        issue_credit_note(
            user=self.user,
            tenant=self.school_a,
            student=self.student_a,
            invoice=invoice,
            amount=Decimal("5000.00"),
            reason="Approved bursary",
        )
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student_a), Decimal("45000.00"))

    def test_invoice_issue_requires_permission(self):
        structure = self._approved_structure()
        assignment = assign_fee_structure(
            user=self.user,
            tenant=self.school_a,
            student=self.student_a,
            fee_structure=structure,
        )
        invoice = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)
        self.role.permissions = ["finance.invoice.create"]
        self.role.save(update_fields=["permissions"])

        with self.assertRaises(ValidationError):
            issue_invoice(user=self.user, tenant=self.school_a, invoice=invoice)


class PaymentTests(TestCase):
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
                "finance.allocation.reverse",
                "notifications.setup.manage",
                "notifications.templates.manage",
                "notifications.rules.manage",
            ],
        )
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role)
        year = AcademicYear.objects.create(tenant=self.school_a, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        level = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        category = FeeCategory.objects.create(tenant=self.school_a, name="Tuition", code="TUITION")
        item = FeeItem.objects.create(tenant=self.school_a, category=category, name="Tuition fee", code="TUITION")
        self.student = Student.objects.create(tenant=self.school_a, admission_number="ADM-001", first_name="Amina", last_name="Otieno")
        self.other_student = Student.objects.create(tenant=self.school_a, admission_number="ADM-002", first_name="Brian", last_name="Kiptoo")
        self.payment_method = PaymentMethod.objects.create(tenant=self.school_a, name="Bank transfer", code="BANK")
        self.other_payment_method = PaymentMethod.objects.create(tenant=self.school_a, name="Cash", code="CASH")
        NumberSeries.objects.create(tenant=self.school_a, document_type="INVOICE", prefix="INV-2026-", padding=6)
        NumberSeries.objects.create(tenant=self.school_a, document_type="CREDIT_NOTE", prefix="CRN-2026-", padding=6)
        NumberSeries.objects.create(tenant=self.school_a, document_type="RECEIPT", prefix="RCT-2026-", padding=6)
        NumberSeries.objects.create(tenant=self.school_a, document_type="PAYMENT_REVERSAL", prefix="PRV-2026-", padding=6)

        self.structure = create_fee_structure(user=self.user, tenant=self.school_a, name="Grade 8 2026", academic_year=year, academic_level=level)
        add_fee_structure_line(user=self.user, tenant=self.school_a, fee_structure=self.structure, fee_item=item, amount=Decimal("50000.00"))
        approve_fee_structure(user=self.user, tenant=self.school_a, fee_structure=self.structure)
        assignment = assign_fee_structure(user=self.user, tenant=self.school_a, student=self.student, fee_structure=self.structure)
        self.invoice = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)
        issue_invoice(user=self.user, tenant=self.school_a, invoice=self.invoice)

    def _record_payment(self, amount="50000.00", key="pay-001", student=None, payment_method=None):
        return record_payment(
            user=self.user,
            tenant=self.school_a,
            student=student or self.student,
            payment_method=payment_method or self.payment_method,
            amount=Decimal(amount),
            idempotency_key=key,
        )

    def test_record_payment_issues_a_receipt_and_no_ledger_entry(self):
        payment = self._record_payment()

        self.assertEqual(payment.receipt.receipt_number, "RCT-2026-000001")
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("50000.00"))

    def test_record_payment_notifies_the_students_primary_guardian_when_configured(self):
        guardian = Guardian.objects.create(tenant=self.school_a, first_name="Ann", last_name="Otieno", phone_number="0700000001")
        StudentGuardian.objects.create(tenant=self.school_a, student=self.student, guardian=guardian, relationship="Mother", is_primary=True)
        set_channel_enabled(user=self.user, tenant=self.school_a, channel=NotificationChannel.SMS, enabled=True)
        template = create_notification_template(
            user=self.user, tenant=self.school_a, code="PAYMENT_SMS", name="Payment", channel=NotificationChannel.SMS,
            body="Dear {{ guardian_name }}, payment of {{ amount }} received. Receipt {{ receipt_number }}.",
        )
        create_notification_rule(
            user=self.user, tenant=self.school_a, event_code="finance.payment.received",
            recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS, template=template,
        )

        payment = self._record_payment()

        event = NotificationEvent.objects.get(tenant=self.school_a)
        expand_notification_event(event=event)
        outbox = NotificationOutbox.objects.get(tenant=self.school_a)
        self.assertEqual(outbox.recipient, "0700000001")
        self.assertIn(payment.receipt.receipt_number, outbox.context["body"])

    def test_record_payment_without_notification_setup_does_not_raise(self):
        # No CommunicationChannel/rule configured at all -- publish_notification_event
        # must no-op silently rather than blocking the payment.
        self._record_payment()
        self.assertEqual(NotificationOutbox.objects.filter(tenant=self.school_a).count(), 0)

    def test_record_payment_is_idempotent(self):
        first = self._record_payment()
        second = self._record_payment()

        self.assertEqual(first.id, second.id)
        self.assertEqual(Payment.objects.filter(tenant=self.school_a).count(), 1)
        self.assertEqual(Receipt.objects.filter(tenant=self.school_a).count(), 1)

    def test_record_payment_rejects_reused_key_with_different_amount(self):
        self._record_payment()

        with self.assertRaises(ValidationError):
            self._record_payment(amount="1.00")

    def test_record_payment_rejects_reused_key_with_different_student(self):
        self._record_payment()

        with self.assertRaises(ValidationError):
            self._record_payment(student=self.other_student)

    def test_record_payment_rejects_reused_key_with_different_payment_method(self):
        self._record_payment()

        with self.assertRaises(ValidationError):
            self._record_payment(payment_method=self.other_payment_method)

    def test_unrelated_integrity_error_is_not_masked_as_idempotency_replay(self):
        # A receipt-number collision is a different failure than a duplicate
        # idempotency key (it would only happen from a genuine numbering bug,
        # since _next_number's select_for_update() serializes normal callers).
        # It must propagate as a real error, not be silently treated as if
        # the second, distinct payment request was just a replay of the first.
        first = self._record_payment(key="pay-first")
        series = NumberSeries.objects.get(tenant=self.school_a, document_type="RECEIPT")
        series.next_value = 1  # forces the next receipt number to collide with `first`'s
        series.save(update_fields=["next_value"])

        with self.assertRaises(IntegrityError):
            self._record_payment(key="pay-second")

        self.assertEqual(Payment.objects.filter(tenant=self.school_a).count(), 1)
        self.assertEqual(Payment.objects.get(tenant=self.school_a).id, first.id)

    def test_allocate_payment_reduces_balance_and_reverse_restores_it(self):
        payment = self._record_payment()

        allocation = allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=self.invoice, amount=Decimal("50000.00"))
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("0.00"))

        reverse_allocation(user=self.user, tenant=self.school_a, allocation=allocation, amount=Decimal("50000.00"), reason="Bounced cheque")
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("50000.00"))

    def test_reversed_allocation_frees_the_payment_for_reallocation(self):
        # Correcting a payment applied to the wrong invoice: reverse the
        # wrong allocation, then the same cash must be allocatable again
        # (to the correct invoice, or back to the same one).
        other_assignment = assign_fee_structure(user=self.user, tenant=self.school_a, student=self.student, fee_structure=self._new_structure())
        other_invoice = generate_invoice(user=self.user, tenant=self.school_a, assignment=other_assignment)
        issue_invoice(user=self.user, tenant=self.school_a, invoice=other_invoice)

        payment = self._record_payment()
        wrong_allocation = allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=self.invoice, amount=Decimal("50000.00"))
        reverse_allocation(user=self.user, tenant=self.school_a, allocation=wrong_allocation, amount=Decimal("50000.00"), reason="Wrong invoice")

        correct_allocation = allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=other_invoice, amount=Decimal("50000.00"))

        self.assertIsNotNone(correct_allocation.id)
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("50000.00"))

    def _new_structure(self, name="Transport"):
        year = self.structure.academic_year
        level = self.structure.academic_level
        code = name.upper().replace(" ", "_")
        category = FeeCategory.objects.create(tenant=self.school_a, name=name, code=code)
        item = FeeItem.objects.create(tenant=self.school_a, category=category, name=f"{name} fee", code=code)
        structure = create_fee_structure(user=self.user, tenant=self.school_a, name=f"{name} 2026", academic_year=year, academic_level=level)
        add_fee_structure_line(user=self.user, tenant=self.school_a, fee_structure=structure, fee_item=item, amount=Decimal("50000.00"))
        approve_fee_structure(user=self.user, tenant=self.school_a, fee_structure=structure)
        return structure

    def _issued_invoice_for_new_structure(self, name, student=None):
        assignment = assign_fee_structure(user=self.user, tenant=self.school_a, student=student or self.student, fee_structure=self._new_structure(name))
        invoice = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)
        issue_invoice(user=self.user, tenant=self.school_a, invoice=invoice)
        return invoice

    def test_allocation_cannot_exceed_payment_amount(self):
        payment = self._record_payment(amount="10000.00")

        with self.assertRaises(ValidationError):
            allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=self.invoice, amount=Decimal("10000.01"))

    def test_allocation_cannot_exceed_invoice_outstanding_balance_net_of_credit_notes(self):
        issue_credit_note(user=self.user, tenant=self.school_a, student=self.student, invoice=self.invoice, amount=Decimal("5000.00"), reason="Bursary")
        payment = self._record_payment()

        with self.assertRaises(ValidationError):
            allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=self.invoice, amount=Decimal("45000.01"))

        allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=self.invoice, amount=Decimal("45000.00"))
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("0.00"))

    def test_allocation_requires_issued_invoice(self):
        other_assignment = assign_fee_structure(user=self.user, tenant=self.school_a, student=self.other_student, fee_structure=self.structure)
        draft_invoice = generate_invoice(user=self.user, tenant=self.school_a, assignment=other_assignment)
        payment = self._record_payment(student=self.other_student, key="pay-draft")

        with self.assertRaises(ValidationError):
            allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=draft_invoice, amount=Decimal("100.00"))

    def test_allocation_rejects_payment_and_invoice_for_different_students(self):
        payment = self._record_payment(student=self.other_student, key="pay-other")

        with self.assertRaises(ValidationError):
            allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=self.invoice, amount=Decimal("100.00"))

    def test_reversal_cannot_exceed_allocations_remaining_amount(self):
        payment = self._record_payment()
        allocation = allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=self.invoice, amount=Decimal("50000.00"))
        reverse_allocation(user=self.user, tenant=self.school_a, allocation=allocation, amount=Decimal("20000.00"), reason="Partial refund")

        with self.assertRaises(ValidationError):
            reverse_allocation(user=self.user, tenant=self.school_a, allocation=allocation, amount=Decimal("30000.01"), reason="Too much")

    def test_payment_actions_require_their_own_permission(self):
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])

        with self.assertRaises(ValidationError):
            self._record_payment()

    def test_reverse_payment_with_no_allocations_only_flips_status(self):
        payment = self._record_payment()

        reversal = reverse_payment(user=self.user, tenant=self.school_a, payment=payment, reason="Entered in error")

        self.assertEqual(reversal.reversal_number, "PRV-2026-000001")
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.REVERSED)
        # Unaffected: nothing was ever allocated, so nothing was ever on the
        # ledger; the balance is just the unrelated invoice from setUp.
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("50000.00"))

    def test_reverse_payment_cascades_across_multiple_allocations(self):
        other_invoice = self._issued_invoice_for_new_structure("Transport")
        payment = self._record_payment(amount="100000.00")
        allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=self.invoice, amount=Decimal("50000.00"))
        allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=other_invoice, amount=Decimal("50000.00"))
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("0.00"))

        reverse_payment(user=self.user, tenant=self.school_a, payment=payment, reason="Bounced cheque")

        self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("100000.00"))
        self.assertEqual(PaymentReversal.objects.filter(tenant=self.school_a, payment=payment).count(), 1)

    def test_reverse_payment_cascades_over_an_already_reversed_allocation(self):
        # Distinguishes reverse_payment from reverse_allocation: a payment
        # can still be whole-payment-reversed even if one of its allocations
        # was already individually corrected first -- the cascade simply
        # has nothing left to do for that one and reverses the rest.
        other_invoice = self._issued_invoice_for_new_structure("Transport")
        payment = self._record_payment(amount="100000.00")
        first_allocation = allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=self.invoice, amount=Decimal("50000.00"))
        allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=other_invoice, amount=Decimal("50000.00"))
        reverse_allocation(user=self.user, tenant=self.school_a, allocation=first_allocation, amount=Decimal("50000.00"), reason="Wrong invoice")

        reverse_payment(user=self.user, tenant=self.school_a, payment=payment, reason="Bounced cheque")

        self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("100000.00"))

    def test_reverse_payment_twice_is_rejected(self):
        payment = self._record_payment()
        reverse_payment(user=self.user, tenant=self.school_a, payment=payment, reason="Entered in error")

        with self.assertRaises(ValidationError):
            reverse_payment(user=self.user, tenant=self.school_a, payment=payment, reason="Again")

    def test_cannot_allocate_a_reversed_payment(self):
        payment = self._record_payment()
        reverse_payment(user=self.user, tenant=self.school_a, payment=payment, reason="Entered in error")

        with self.assertRaises(ValidationError):
            allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=self.invoice, amount=Decimal("100.00"))

    def test_payment_reversal_requires_its_own_permission(self):
        self.role.permissions = ["finance.payment.record"]
        self.role.save(update_fields=["permissions"])
        payment = self._record_payment()
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])

        with self.assertRaises(ValidationError):
            reverse_payment(user=self.user, tenant=self.school_a, payment=payment, reason="Entered in error")


class ReconciliationTests(TestCase):
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
                "finance.payment.record",
                "finance.payment.allocate",
                "finance.reconciliation.ingest",
                "finance.reconciliation.match",
                "finance.reconciliation.ignore",
            ],
        )
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role)
        year = AcademicYear.objects.create(tenant=self.school_a, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        level = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        category = FeeCategory.objects.create(tenant=self.school_a, name="Tuition", code="TUITION")
        item = FeeItem.objects.create(tenant=self.school_a, category=category, name="Tuition fee", code="TUITION")
        self.student = Student.objects.create(tenant=self.school_a, admission_number="ADM-001", first_name="Amina", last_name="Otieno")
        self.other_student = Student.objects.create(tenant=self.school_a, admission_number="ADM-002", first_name="Brian", last_name="Kiptoo")
        # Deliberately prefix-colliding with self.student's admission number,
        # so a reference containing "ADM-0010" matches both -- the ambiguous
        # case the recognition heuristic must handle by staying UNMATCHED.
        self.colliding_student = Student.objects.create(tenant=self.school_a, admission_number="ADM-0010", first_name="Cynthia", last_name="Wanjiru")
        self.payment_method = PaymentMethod.objects.create(tenant=self.school_a, name="Bank transfer", code="BANK")
        NumberSeries.objects.create(tenant=self.school_a, document_type="INVOICE", prefix="INV-2026-", padding=6)
        NumberSeries.objects.create(tenant=self.school_a, document_type="RECEIPT", prefix="RCT-2026-", padding=6)

        self.structure = create_fee_structure(user=self.user, tenant=self.school_a, name="Grade 8 2026", academic_year=year, academic_level=level)
        add_fee_structure_line(user=self.user, tenant=self.school_a, fee_structure=self.structure, fee_item=item, amount=Decimal("50000.00"))
        approve_fee_structure(user=self.user, tenant=self.school_a, fee_structure=self.structure)
        assignment = assign_fee_structure(user=self.user, tenant=self.school_a, student=self.student, fee_structure=self.structure)
        self.invoice = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)
        issue_invoice(user=self.user, tenant=self.school_a, invoice=self.invoice)

    def _ingest(self, amount="1000.00", reference="", transaction_id="bank-001"):
        return ingest_incoming_payment(
            user=self.user,
            tenant=self.school_a,
            payment_method=self.payment_method,
            amount=Decimal(amount),
            external_reference=reference,
            external_transaction_id=transaction_id,
        )

    def test_ingest_with_no_reference_stays_unmatched(self):
        incoming = self._ingest(reference="")

        self.assertEqual(incoming.status, ReconciliationStatus.UNMATCHED)
        self.assertIsNone(incoming.matched_payment)

    def test_ingest_with_reference_matching_one_student_auto_matches(self):
        incoming = self._ingest(amount="1234.00", reference="BANK/ADM-002/JAN", transaction_id="bank-002")

        self.assertEqual(incoming.status, ReconciliationStatus.MATCHED)
        self.assertIsNotNone(incoming.matched_payment)
        self.assertEqual(incoming.matched_payment.student_id, self.other_student.id)
        self.assertEqual(incoming.matched_payment.idempotency_key, f"incoming-payment:{incoming.id}")

    def test_ingest_with_ambiguous_reference_stays_unmatched(self):
        # "ADM-0010" contains "ADM-001" as a substring, so both self.student
        # and self.colliding_student match -- ambiguous, must not auto-match.
        incoming = self._ingest(reference="BANK/ADM-0010/JAN", transaction_id="bank-003")

        self.assertEqual(incoming.status, ReconciliationStatus.UNMATCHED)
        self.assertIsNone(incoming.matched_payment)

    def test_auto_match_with_single_exact_amount_invoice_auto_allocates(self):
        incoming = self._ingest(amount="50000.00", reference="BANK/ADM-001/JAN", transaction_id="bank-004")

        self.assertEqual(incoming.status, ReconciliationStatus.MATCHED)
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("0.00"))
        self.assertEqual(incoming.matched_payment.allocations.count(), 1)

    def test_auto_match_with_no_exact_amount_candidate_leaves_payment_unapplied(self):
        # A successful reconciliation outcome, not a failure: the student is
        # identified, the Payment is created and RECEIVED, but the amount
        # doesn't exactly match the one outstanding invoice, so it's left
        # for a bursar to allocate manually.
        incoming = self._ingest(amount="12345.00", reference="BANK/ADM-001/JAN", transaction_id="bank-005")

        self.assertEqual(incoming.status, ReconciliationStatus.MATCHED)
        payment = incoming.matched_payment
        self.assertEqual(payment.status, PaymentStatus.RECEIVED)
        self.assertEqual(payment.allocations.count(), 0)
        self.assertEqual(student_balance(tenant=self.school_a, student=self.student), Decimal("50000.00"))

    def test_manual_match_on_unmatched_entry(self):
        incoming = self._ingest(amount="50000.00", reference="")

        matched = match_incoming_payment(user=self.user, tenant=self.school_a, incoming=incoming, student=self.student)

        self.assertEqual(matched.status, ReconciliationStatus.MATCHED)
        self.assertEqual(matched.matched_payment.student_id, self.student.id)

    def test_matching_an_already_resolved_entry_is_rejected(self):
        incoming = self._ingest(reference="")
        match_incoming_payment(user=self.user, tenant=self.school_a, incoming=incoming, student=self.student)

        with self.assertRaises(ValidationError):
            match_incoming_payment(user=self.user, tenant=self.school_a, incoming=incoming, student=self.other_student)

    def test_ignoring_an_already_resolved_entry_is_rejected(self):
        incoming = self._ingest(reference="")
        match_incoming_payment(user=self.user, tenant=self.school_a, incoming=incoming, student=self.student)

        with self.assertRaises(ValidationError):
            ignore_incoming_payment(user=self.user, tenant=self.school_a, incoming=incoming, reason="Too late")

    def test_ignore_sets_status_and_reason(self):
        incoming = self._ingest(reference="")

        ignored = ignore_incoming_payment(user=self.user, tenant=self.school_a, incoming=incoming, reason="Bank fee, not a student payment")

        self.assertEqual(ignored.status, ReconciliationStatus.IGNORED)
        self.assertEqual(ignored.ignored_reason, "Bank fee, not a student payment")
        self.assertIsNone(ignored.matched_payment)

    def test_ingest_is_idempotent(self):
        first = self._ingest(transaction_id="bank-dup")
        second = self._ingest(transaction_id="bank-dup")

        self.assertEqual(first.id, second.id)
        self.assertEqual(IncomingPayment.objects.filter(tenant=self.school_a).count(), 1)

    def test_ingest_rejects_reused_transaction_id_with_different_amount(self):
        self._ingest(amount="1000.00", transaction_id="bank-dup2")

        with self.assertRaises(ValidationError):
            self._ingest(amount="2000.00", transaction_id="bank-dup2")

    def test_ingest_rejects_a_payment_method_from_a_different_tenant(self):
        foreign_method = PaymentMethod.objects.create(tenant=self.school_b, name="Foreign", code="FOREIGN")

        with self.assertRaises(ValidationError):
            ingest_incoming_payment(
                user=self.user, tenant=self.school_a, payment_method=foreign_method,
                amount=Decimal("10.00"), external_reference="", external_transaction_id="bank-006",
            )
        self.assertEqual(IncomingPayment.objects.filter(tenant=self.school_a).count(), 0)

    def test_reconciliation_actions_require_their_own_permission(self):
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])

        with self.assertRaises(ValidationError):
            self._ingest()

    def test_match_and_ignore_require_their_own_permission(self):
        incoming = self._ingest(reference="")
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])

        with self.assertRaises(ValidationError):
            match_incoming_payment(user=self.user, tenant=self.school_a, incoming=incoming, student=self.student)
        with self.assertRaises(ValidationError):
            ignore_incoming_payment(user=self.user, tenant=self.school_a, incoming=incoming, reason="x")

    def test_provenance_round_trip_from_payment_to_incoming_payment(self):
        incoming = self._ingest(amount="777.00", reference="", transaction_id="bank-provenance")
        matched = match_incoming_payment(user=self.user, tenant=self.school_a, incoming=incoming, student=self.student)

        payment = matched.matched_payment
        self.assertEqual(payment.incoming_payment.external_transaction_id, "bank-provenance")
        # The raw external id is never copied onto Payment itself -- the
        # OneToOne relationship is the sole place provenance lives.
        self.assertNotEqual(payment.external_reference, "bank-provenance")


class ReportSelectorTests(PaymentTests):
    """Milestone 20 -- the query functions apps.reporting's catalogue calls
    for finance.fee_statement/finance.collections_summary.
    """

    def test_fee_statement_rows_reflects_invoice_debit_and_payment_credit(self):
        payment = self._record_payment()
        allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=self.invoice, amount=Decimal("50000.00"))
        rows = fee_statement_rows(tenant=self.school_a, student_id=str(self.student.id), as_of=date(2100, 1, 1))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["debit"], Decimal("50000.00"))
        self.assertIn("Invoice", rows[0]["description"])
        self.assertEqual(rows[1]["credit"], Decimal("50000.00"))
        self.assertEqual(rows[-1]["running_balance"], Decimal("0.00"))

    def test_fee_statement_rows_excludes_entries_after_as_of(self):
        self._record_payment()
        rows = fee_statement_rows(tenant=self.school_a, student_id=str(self.student.id), as_of=date(2000, 1, 1))
        self.assertEqual(rows, [])

    def test_fee_statement_rows_rejects_a_student_outside_the_tenant(self):
        other_tenant = Tenant.objects.create(name="Other", slug="other")
        outsider = Student.objects.create(tenant=other_tenant, admission_number="X-1", first_name="X", last_name="Y")
        with self.assertRaises(ValidationError):
            fee_statement_rows(tenant=self.school_a, student_id=str(outsider.id), as_of=date(2100, 1, 1))

    def test_collections_summary_rows_aggregates_by_date_and_method(self):
        self._record_payment()
        rows = collections_summary_rows(tenant=self.school_a, start_date=date(2000, 1, 1), end_date=date(2100, 1, 1))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["payment_method"], "Bank transfer")
        self.assertEqual(rows[0]["payment_count"], 1)
        self.assertEqual(rows[0]["total_amount"], Decimal("50000.00"))

    def test_collections_summary_rows_excludes_reversed_payments(self):
        payment = self._record_payment()
        reverse_payment(user=self.user, tenant=self.school_a, payment=payment, reason="Bounced cheque")
        rows = collections_summary_rows(tenant=self.school_a, start_date=date(2000, 1, 1), end_date=date(2100, 1, 1))
        self.assertEqual(rows, [])

    def test_fee_statement_rows_limit_bounds_the_returned_rows(self):
        payment = self._record_payment()
        allocate_payment(user=self.user, tenant=self.school_a, payment=payment, invoice=self.invoice, amount=Decimal("50000.00"))
        rows = fee_statement_rows(tenant=self.school_a, student_id=str(self.student.id), as_of=date(2100, 1, 1), limit=1)
        self.assertEqual(len(rows), 1)
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.activity.services import record_activity
from apps.tenancy.services import require_permission

from .models import (
    AllocationReversal,
    CreditNote,
    CreditNoteStatus,
    FeeAssignmentStatus,
    FeeStructure,
    FeeStructureLine,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    LedgerEntryType,
    NumberSeries,
    Payment,
    PaymentAllocation,
    PaymentReversal,
    PaymentStatus,
    Receipt,
    StudentFeeAssignment,
    StudentLedgerEntry,
    validate_same_tenant,
)


def _next_number(*, tenant, document_type):
    try:
        series = NumberSeries.objects.select_for_update().get(tenant=tenant, document_type=document_type)
    except NumberSeries.DoesNotExist as error:
        raise ValidationError(f"Number series is not configured: {document_type}") from error
    number = series.preview()
    series.next_value += 1
    series.save(update_fields=["next_value"])
    return number


def create_fee_structure(*, user, tenant, name, academic_year, academic_level):
    require_permission(user=user, tenant=tenant, permission="finance.fee_structure.create")
    validate_same_tenant(tenant=tenant, academic_year=academic_year, academic_level=academic_level)
    return FeeStructure.objects.create(
        tenant=tenant,
        name=name,
        academic_year=academic_year,
        academic_level=academic_level,
    )


def add_fee_structure_line(*, user, tenant, fee_structure, fee_item, amount, is_required=True):
    require_permission(user=user, tenant=tenant, permission="finance.fee_structure.edit")
    validate_same_tenant(tenant=tenant, fee_structure=fee_structure, fee_item=fee_item)
    if fee_structure.is_approved:
        raise ValidationError("Approved fee structures cannot be edited")
    if amount <= 0:
        raise ValidationError("Fee amount must be greater than zero")
    return FeeStructureLine.objects.create(
        tenant=tenant,
        fee_structure=fee_structure,
        fee_item=fee_item,
        amount=amount,
        is_required=is_required,
    )


def approve_fee_structure(*, user, tenant, fee_structure):
    require_permission(user=user, tenant=tenant, permission="finance.fee_structure.approve")
    validate_same_tenant(tenant=tenant, fee_structure=fee_structure)
    if not fee_structure.lines.exists():
        raise ValidationError("A fee structure must have at least one line before approval")
    fee_structure.is_approved = True
    fee_structure.save(update_fields=["is_approved"])
    return fee_structure


def assign_fee_structure(*, user, tenant, student, fee_structure):
    require_permission(user=user, tenant=tenant, permission="finance.invoice.create")
    validate_same_tenant(tenant=tenant, student=student, fee_structure=fee_structure)
    if not fee_structure.is_approved:
        raise ValidationError("Only approved fee structures can be assigned")
    assignment, _ = StudentFeeAssignment.objects.get_or_create(
        tenant=tenant,
        student=student,
        fee_structure=fee_structure,
        defaults={"status": FeeAssignmentStatus.ACTIVE},
    )
    return assignment


@transaction.atomic
def generate_invoice(*, user, tenant, assignment):
    require_permission(user=user, tenant=tenant, permission="finance.invoice.create")
    locked_assignment = (
        StudentFeeAssignment.objects.select_for_update()
        .select_related("student", "fee_structure")
        .get(pk=assignment.pk)
    )
    validate_same_tenant(tenant=tenant, assignment=locked_assignment)
    if locked_assignment.status != FeeAssignmentStatus.ACTIVE:
        raise ValidationError("Only active fee assignments can generate invoices")
    existing = Invoice.objects.filter(tenant=tenant, assignment=locked_assignment).first()
    if existing is not None:
        return existing
    if not locked_assignment.fee_structure.is_approved:
        raise ValidationError("Only approved fee structures can generate invoices")
    try:
        invoice = Invoice.objects.create(
            tenant=tenant,
            invoice_number=_next_number(tenant=tenant, document_type="INVOICE"),
            idempotency_key=f"fee-assignment:{locked_assignment.id}",
            student=locked_assignment.student,
            assignment=locked_assignment,
        )
        lines = list(
            FeeStructureLine.objects.filter(
                tenant=tenant,
                fee_structure=locked_assignment.fee_structure,
            ).select_related("fee_item")
        )
        if not lines:
            raise ValidationError("A fee structure must have lines before invoicing")
        subtotal = Decimal("0")
        for source_line in lines:
            net_amount = source_line.amount
            InvoiceLine.objects.create(
                tenant=tenant,
                invoice=invoice,
                fee_item=source_line.fee_item,
                source_fee_structure_line=source_line,
                description=source_line.fee_item.name,
                quantity=Decimal("1"),
                unit_amount=source_line.amount,
                net_amount=net_amount,
            )
            subtotal += net_amount
        invoice.subtotal = subtotal
        invoice.total = subtotal
        invoice.save(update_fields=["subtotal", "total"])
        return invoice
    except IntegrityError as error:
        raise ValidationError("Invoice generation conflicted with another request") from error


@transaction.atomic
def issue_invoice(*, user, tenant, invoice):
    require_permission(user=user, tenant=tenant, permission="finance.invoice.issue")
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    validate_same_tenant(tenant=tenant, invoice=invoice)
    if invoice.status != InvoiceStatus.DRAFT:
        raise ValidationError("Only draft invoices can be issued")
    if invoice.total <= 0 or not invoice.lines.exists():
        raise ValidationError("Invoice must contain positive lines before issue")
    invoice.status = InvoiceStatus.ISSUED
    invoice.issued_at = timezone.now()
    invoice.save(update_fields=["status", "issued_at"])
    StudentLedgerEntry.objects.get_or_create(
        tenant=tenant,
        invoice=invoice,
        defaults={
            "student": invoice.student,
            "entry_type": LedgerEntryType.DEBIT,
            "amount": invoice.total,
        },
    )
    record_activity(
        tenant=tenant,
        actor=user,
        action="invoice.issued",
        resource_type="invoice",
        resource_id=str(invoice.id),
    )
    return invoice


@transaction.atomic
def issue_credit_note(*, user, tenant, student, amount, reason, invoice=None):
    require_permission(user=user, tenant=tenant, permission="finance.credit_note.create")
    if amount <= 0:
        raise ValidationError("Credit note amount must be greater than zero")
    validate_same_tenant(tenant=tenant, student=student)
    if invoice is not None:
        validate_same_tenant(tenant=tenant, invoice=invoice)
        # Any operation deciding whether more value can be applied against an
        # invoice must hold that invoice's row lock while making and recording
        # the decision -- otherwise two concurrent credit notes (or a credit
        # note racing a payment allocation) can each read a stale balance and
        # together exceed it. allocate_payment() follows the same protocol.
        try:
            invoice = Invoice.objects.select_for_update().get(tenant=tenant, pk=invoice.pk)
        except Invoice.DoesNotExist as error:
            raise ValidationError("Invoice is not available in this tenant") from error
        if invoice.student_id != student.id:
            raise ValidationError("Credit note invoice must belong to the student")
        issued_credits = invoice.credit_notes.filter(status=CreditNoteStatus.ISSUED).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        if amount > invoice.total - issued_credits:
            raise ValidationError("Credit note exceeds the invoice balance")
    credit_note = CreditNote.objects.create(
        tenant=tenant,
        credit_note_number=_next_number(tenant=tenant, document_type="CREDIT_NOTE"),
        student=student,
        invoice=invoice,
        reason=reason,
        amount=amount,
        status=CreditNoteStatus.ISSUED,
        issued_at=timezone.now(),
    )
    StudentLedgerEntry.objects.create(
        tenant=tenant,
        student=student,
        credit_note=credit_note,
        entry_type=LedgerEntryType.CREDIT,
        amount=amount,
    )
    record_activity(
        tenant=tenant,
        actor=user,
        action="credit_note.issued",
        resource_type="credit_note",
        resource_id=str(credit_note.id),
    )
    return credit_note


def _matching_replay(*, existing, student, payment_method, amount):
    if existing.student_id != student.id or existing.amount != amount or existing.payment_method_id != payment_method.id:
        raise ValidationError("Idempotency key already used with different payment details")
    return existing


@transaction.atomic
def record_payment(*, user, tenant, student, payment_method, amount, idempotency_key, external_reference="", received_at=None):
    require_permission(user=user, tenant=tenant, permission="finance.payment.record")
    if amount <= 0:
        raise ValidationError("Payment amount must be greater than zero")
    validate_same_tenant(tenant=tenant, student=student, payment_method=payment_method)
    existing = Payment.objects.filter(tenant=tenant, idempotency_key=idempotency_key).first()
    if existing is not None:
        return _matching_replay(existing=existing, student=student, payment_method=payment_method, amount=amount)
    try:
        # A nested atomic() creates a savepoint: on IntegrityError, Django rolls
        # back only to it, leaving the outer transaction usable for the replay
        # lookup below. Without this, PostgreSQL marks the whole transaction
        # aborted and that lookup would itself fail.
        with transaction.atomic():
            payment = Payment.objects.create(
                tenant=tenant,
                student=student,
                payment_method=payment_method,
                amount=amount,
                idempotency_key=idempotency_key,
                external_reference=external_reference,
                received_at=received_at or timezone.now(),
            )
            Receipt.objects.create(
                tenant=tenant,
                payment=payment,
                receipt_number=_next_number(tenant=tenant, document_type="RECEIPT"),
            )
    except IntegrityError as error:
        # Only translate the idempotency-key collision, not unrelated failures
        # (e.g. a genuine receipt-numbering bug) into a business validation error.
        cause = error.__cause__
        constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
        sqlite_duplicate = str(cause) == "UNIQUE constraint failed: finance_payment.tenant_id, finance_payment.idempotency_key"
        if constraint != "unique_payment_idempotency_per_tenant" and not sqlite_duplicate:
            raise
        replay = Payment.objects.filter(tenant=tenant, idempotency_key=idempotency_key).first()
        if replay is None:
            raise ValidationError("Payment recording conflicted with another request") from error
        return _matching_replay(existing=replay, student=student, payment_method=payment_method, amount=amount)
    record_activity(
        tenant=tenant,
        actor=user,
        action="payment.recorded",
        resource_type="payment",
        resource_id=str(payment.id),
    )
    return payment


@transaction.atomic
def allocate_payment(*, user, tenant, payment, invoice, amount):
    require_permission(user=user, tenant=tenant, permission="finance.payment.allocate")
    if amount <= 0:
        raise ValidationError("Allocation amount must be greater than zero")
    validate_same_tenant(tenant=tenant, payment=payment, invoice=invoice)
    try:
        locked_payment = Payment.objects.select_for_update().get(tenant=tenant, pk=payment.pk)
        locked_invoice = Invoice.objects.select_for_update().get(tenant=tenant, pk=invoice.pk)
    except (Payment.DoesNotExist, Invoice.DoesNotExist) as error:
        raise ValidationError("Payment or invoice is not available in this tenant") from error
    if locked_invoice.student_id != locked_payment.student_id:
        raise ValidationError("Payment and invoice must belong to the same student")
    if locked_invoice.status != InvoiceStatus.ISSUED:
        raise ValidationError("Only issued invoices can receive payment allocations")
    if locked_payment.status == PaymentStatus.REVERSED:
        raise ValidationError("Cannot allocate a reversed payment")

    # Net of reversals: reversing an allocation must free that cash for
    # reallocation (e.g. correcting a payment applied to the wrong invoice),
    # so a reversed allocation cannot keep counting against the payment.
    allocated_from_payment = locked_payment.allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    reversed_from_payment = AllocationReversal.objects.filter(tenant=tenant, allocation__payment=locked_payment).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    if amount > locked_payment.amount - (allocated_from_payment - reversed_from_payment):
        raise ValidationError("Allocation exceeds the payment's unallocated amount")

    issued_credits = locked_invoice.credit_notes.filter(status=CreditNoteStatus.ISSUED).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    allocated_to_invoice = locked_invoice.payment_allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    reversed_for_invoice = AllocationReversal.objects.filter(tenant=tenant, allocation__invoice=locked_invoice).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    net_paid = allocated_to_invoice - reversed_for_invoice
    if amount > locked_invoice.total - issued_credits - net_paid:
        raise ValidationError("Allocation exceeds the invoice's outstanding balance")

    allocation = PaymentAllocation.objects.create(
        tenant=tenant,
        payment=locked_payment,
        invoice=locked_invoice,
        amount=amount,
    )
    StudentLedgerEntry.objects.create(
        tenant=tenant,
        student=locked_payment.student,
        payment_allocation=allocation,
        entry_type=LedgerEntryType.CREDIT,
        amount=amount,
    )
    record_activity(
        tenant=tenant,
        actor=user,
        action="payment.allocated",
        resource_type="payment_allocation",
        resource_id=str(allocation.id),
    )
    return allocation


def _post_allocation_reversal(*, tenant, allocation, amount, reason, student):
    reversal = AllocationReversal.objects.create(
        tenant=tenant,
        allocation=allocation,
        amount=amount,
        reason=reason,
    )
    StudentLedgerEntry.objects.create(
        tenant=tenant,
        student=student,
        allocation_reversal=reversal,
        entry_type=LedgerEntryType.DEBIT,
        amount=amount,
    )
    return reversal


@transaction.atomic
def reverse_allocation(*, user, tenant, allocation, amount, reason):
    require_permission(user=user, tenant=tenant, permission="finance.allocation.reverse")
    if amount <= 0:
        raise ValidationError("Reversal amount must be greater than zero")
    validate_same_tenant(tenant=tenant, allocation=allocation)
    try:
        locked_payment = Payment.objects.select_for_update().get(tenant=tenant, pk=allocation.payment_id)
        locked_invoice = Invoice.objects.select_for_update().get(tenant=tenant, pk=allocation.invoice_id)
        locked_allocation = PaymentAllocation.objects.select_for_update().get(tenant=tenant, pk=allocation.pk)
    except (Payment.DoesNotExist, Invoice.DoesNotExist, PaymentAllocation.DoesNotExist) as error:
        raise ValidationError("Allocation is not available in this tenant") from error

    reversed_total = locked_allocation.reversals.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    if amount > locked_allocation.amount - reversed_total:
        raise ValidationError("Reversal exceeds the allocation's remaining amount")

    reversal = _post_allocation_reversal(tenant=tenant, allocation=locked_allocation, amount=amount, reason=reason, student=locked_payment.student)
    record_activity(
        tenant=tenant,
        actor=user,
        action="payment.allocation_reversed",
        resource_type="allocation_reversal",
        resource_id=str(reversal.id),
    )
    return reversal


@transaction.atomic
def reverse_payment(*, user, tenant, payment, reason):
    """Invalidate the payment itself, cascading to reverse every currently
    active allocation on it in one action (see AllocationReversal for the
    narrower, single-allocation correction this reuses).
    """
    require_permission(user=user, tenant=tenant, permission="finance.payment.reverse")
    validate_same_tenant(tenant=tenant, payment=payment)
    try:
        locked_payment = Payment.objects.select_for_update().get(tenant=tenant, pk=payment.pk)
    except Payment.DoesNotExist as error:
        raise ValidationError("Payment is not available in this tenant") from error
    if locked_payment.status == PaymentStatus.REVERSED:
        raise ValidationError("Payment has already been reversed")

    # Lock every affected invoice in a fixed ascending order before touching
    # any of them: this is the first path that may lock more than one
    # invoice in a single transaction, so a deterministic order is required
    # to stay deadlock-free against another concurrent reverse_payment()
    # whose allocations overlap the same invoices.
    allocations = list(
        PaymentAllocation.objects.filter(tenant=tenant, payment=locked_payment).select_for_update().order_by("invoice_id")
    )
    for allocation in allocations:
        Invoice.objects.select_for_update().get(tenant=tenant, pk=allocation.invoice_id)

    for allocation in allocations:
        reversed_total = allocation.reversals.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        remaining = allocation.amount - reversed_total
        if remaining > 0:
            reversal = _post_allocation_reversal(tenant=tenant, allocation=allocation, amount=remaining, reason=reason, student=locked_payment.student)
            record_activity(
                tenant=tenant,
                actor=user,
                action="payment.allocation_reversed",
                resource_type="allocation_reversal",
                resource_id=str(reversal.id),
            )

    payment_reversal = PaymentReversal.objects.create(
        tenant=tenant,
        payment=locked_payment,
        reversal_number=_next_number(tenant=tenant, document_type="PAYMENT_REVERSAL"),
        reason=reason,
    )
    locked_payment.status = PaymentStatus.REVERSED
    locked_payment.save(update_fields=["status"])
    record_activity(
        tenant=tenant,
        actor=user,
        action="payment.reversed",
        resource_type="payment_reversal",
        resource_id=str(payment_reversal.id),
    )
    return payment_reversal
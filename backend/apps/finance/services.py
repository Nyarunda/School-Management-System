from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.activity.services import record_activity
from apps.notifications.services import publish_notification_event
from apps.students.models import Student
from apps.tenancy.services import require_permission

from .models import (
    AllocationReversal,
    CreditNote,
    CreditNoteStatus,
    FeeAssignmentStatus,
    FeeStructure,
    FeeStructureLine,
    IncomingPayment,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    LedgerEntryType,
    NumberSeries,
    Payment,
    PaymentAllocation,
    PaymentReversal,
    PaymentStatus,
    ReconciliationStatus,
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


@transaction.atomic
def approve_fee_structure(*, user, tenant, fee_structure):
    require_permission(user=user, tenant=tenant, permission="finance.fee_structure.approve")
    validate_same_tenant(tenant=tenant, fee_structure=fee_structure)
    if not fee_structure.lines.exists():
        raise ValidationError("A fee structure must have at least one line before approval")
    fee_structure.is_approved = True
    fee_structure.save(update_fields=["is_approved"])
    record_activity(
        tenant=tenant,
        actor=user,
        action="fee_structure.approved",
        resource_type="fee_structure",
        resource_id=str(fee_structure.id),
    )
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
        record_activity(
            tenant=tenant,
            actor=user,
            action="invoice.generated",
            resource_type="invoice",
            resource_id=str(invoice.id),
        )
        return invoice
    except IntegrityError as error:
        # Only translate the idempotency-key collision, not unrelated failures
        # (e.g. a genuine invoice-numbering bug), into a business validation
        # error -- matches record_payment's/ingest_incoming_payment's pattern.
        # In practice this is a narrow defensive backstop: the StudentFeeAssignment
        # lock above already prevents two concurrent calls for the *same*
        # assignment from racing into this block.
        cause = error.__cause__
        constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
        sqlite_duplicate = str(cause) == "UNIQUE constraint failed: finance_invoice.tenant_id, finance_invoice.idempotency_key"
        if constraint != "unique_invoice_idempotency_per_tenant" and not sqlite_duplicate:
            raise
        replay = Invoice.objects.filter(tenant=tenant, assignment=locked_assignment).first()
        if replay is None:
            raise ValidationError("Invoice generation conflicted with another request") from error
        return replay


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
            receipt = Receipt.objects.create(
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
    publish_notification_event(
        tenant=tenant, event_code="finance.payment.received", dedupe_key=f"payment-received:{payment.id}",
        context={"amount": str(amount), "receipt_number": receipt.receipt_number},
        recipient_refs={"student": student}, actor=user,
    )
    return payment


def _invoice_outstanding_balance(*, tenant, invoice):
    issued_credits = invoice.credit_notes.filter(status=CreditNoteStatus.ISSUED).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    allocated = invoice.payment_allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    reversed_amount = AllocationReversal.objects.filter(tenant=tenant, allocation__invoice=invoice).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return invoice.total - issued_credits - (allocated - reversed_amount)


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

    if amount > _invoice_outstanding_balance(tenant=tenant, invoice=locked_invoice):
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


def _bulk_outstanding_balances(*, tenant, invoices):
    """Outstanding balance for many invoices in a fixed 3 queries total, not
    one aggregate per invoice (an N+1) and not a fan-out multi-join
    annotation (joining credit_notes + payment_allocations + reversals onto
    one queryset multiplies rows and silently inflates every sum). Used only
    for the auto-allocate candidate scan below; the actual allocation still
    goes through allocate_payment's own single-invoice lock and check via
    _invoice_outstanding_balance.
    """
    invoice_ids = [invoice.id for invoice in invoices]
    credits_by_invoice = dict(
        CreditNote.objects.filter(tenant=tenant, invoice_id__in=invoice_ids, status=CreditNoteStatus.ISSUED)
        .values("invoice_id").annotate(total=Sum("amount")).values_list("invoice_id", "total")
    )
    allocated_by_invoice = dict(
        PaymentAllocation.objects.filter(tenant=tenant, invoice_id__in=invoice_ids)
        .values("invoice_id").annotate(total=Sum("amount")).values_list("invoice_id", "total")
    )
    reversed_by_invoice = dict(
        AllocationReversal.objects.filter(tenant=tenant, allocation__invoice_id__in=invoice_ids)
        .values("allocation__invoice_id").annotate(total=Sum("amount")).values_list("allocation__invoice_id", "total")
    )
    return {
        invoice.id: invoice.total
            - (credits_by_invoice.get(invoice.id) or Decimal("0"))
            - ((allocated_by_invoice.get(invoice.id) or Decimal("0")) - (reversed_by_invoice.get(invoice.id) or Decimal("0")))
        for invoice in invoices
    }


def _recognize_student_from_reference(*, tenant, reference):
    """Recognition strategy v1: case-insensitive admission-number substring
    match. A later v2 (structured reference codes, a known-payer mapping, a
    gateway's own account-reference field) should replace or extend this
    single function rather than growing fuzzy-matching logic elsewhere in
    reconciliation. Short or prefix-colliding admission numbers can make two
    students both match the same reference (e.g. ADM-123 and ADM-1234
    against ".../ADM-1234/..."); that is handled safely by the
    ambiguous-stays-UNMATCHED rule in callers, not by this function. O(n)
    over the tenant's students -- fine at expected school-roster scale;
    revisit with a DB-assisted search if a tenant's roster ever makes this a
    hot path.
    """
    if not reference:
        return None
    normalized = reference.strip().upper()
    candidates = [
        student for student in Student.objects.for_tenant(tenant).only("id", "admission_number")
        if student.admission_number and student.admission_number.upper() in normalized
    ]
    return candidates[0] if len(candidates) == 1 else None


def _attempt_auto_allocate(*, user, tenant, payment, student):
    invoices = list(Invoice.objects.for_tenant(tenant).filter(student=student, status=InvoiceStatus.ISSUED))
    if not invoices:
        return
    balances = _bulk_outstanding_balances(tenant=tenant, invoices=invoices)
    candidates = [invoice for invoice in invoices if balances[invoice.id] == payment.amount]
    if len(candidates) != 1:
        return
    try:
        allocate_payment(user=user, tenant=tenant, payment=payment, invoice=candidates[0], amount=payment.amount)
    except ValidationError:
        # Leave as unapplied cash -- a fully valid, successful reconciliation
        # outcome, not a failure. allocate_payment is its own atomic()
        # (a savepoint here), so this can't poison the outer transaction.
        pass


def _complete_match(*, user, tenant, incoming, student):
    """Shared by automatic (reference-recognition) and manual (bursar-
    confirmed) matching. `incoming` must already be locked by the caller
    (select_for_update, tenant-scoped) -- this re-checks status itself as
    the single choke point that creates money, but does not take the lock
    and does not check permissions (callers own both).
    """
    if incoming.status != ReconciliationStatus.UNMATCHED:
        raise ValidationError("Incoming payment has already been resolved")
    payment = record_payment(
        user=user,
        tenant=tenant,
        student=student,
        payment_method=incoming.payment_method,
        amount=incoming.amount,
        idempotency_key=f"incoming-payment:{incoming.id}",
        external_reference=incoming.external_reference,
        received_at=incoming.received_at,
    )
    incoming.status = ReconciliationStatus.MATCHED
    incoming.matched_payment = payment
    incoming.save(update_fields=["status", "matched_payment"])
    record_activity(
        tenant=tenant,
        actor=user,
        action="incoming_payment.matched",
        resource_type="incoming_payment",
        resource_id=str(incoming.id),
    )
    _attempt_auto_allocate(user=user, tenant=tenant, payment=payment, student=student)
    return incoming


def _attempt_auto_match(*, user, tenant, incoming):
    """Called from inside ingest_incoming_payment's own transaction, right
    after `incoming` was created there -- no other transaction can see or
    lock this row yet. It still goes through the same lock-then-transition
    protocol as match_incoming_payment()/ignore_incoming_payment(), so
    UNMATCHED -> {MATCHED, IGNORED} is serialized identically everywhere,
    including once a background reconciliation job exists alongside manual
    bursar actions.
    """
    student = _recognize_student_from_reference(tenant=tenant, reference=incoming.external_reference)
    if student is None:
        return
    try:
        locked_incoming = IncomingPayment.objects.select_for_update().get(tenant=tenant, pk=incoming.pk)
        _complete_match(user=user, tenant=tenant, incoming=locked_incoming, student=student)
    except ValidationError:
        return  # leave UNMATCHED; a bursar resolves it manually
    incoming.refresh_from_db()


def _matching_incoming_replay(*, existing, payment_method, amount):
    if existing.payment_method_id != payment_method.id or existing.amount != amount:
        raise ValidationError("External transaction id already used with different payment details")
    return existing


@transaction.atomic
def ingest_incoming_payment(*, user, tenant, payment_method, amount, external_reference, external_transaction_id, received_at=None):
    """Mirrors record_payment()'s idempotency shape exactly: an early
    existence check, then a nested atomic() (savepoint) around the risky
    insert so an IntegrityError-recovery replay lookup can't itself fail on
    an aborted PostgreSQL transaction, with the specific constraint name
    checked so an unrelated integrity failure isn't masked as a replay.
    """
    require_permission(user=user, tenant=tenant, permission="finance.reconciliation.ingest")
    if amount <= 0:
        raise ValidationError("Incoming payment amount must be greater than zero")
    validate_same_tenant(tenant=tenant, payment_method=payment_method)
    existing = IncomingPayment.objects.filter(tenant=tenant, external_transaction_id=external_transaction_id).first()
    if existing is not None:
        return _matching_incoming_replay(existing=existing, payment_method=payment_method, amount=amount)
    try:
        with transaction.atomic():
            incoming = IncomingPayment.objects.create(
                tenant=tenant,
                payment_method=payment_method,
                amount=amount,
                external_reference=external_reference,
                external_transaction_id=external_transaction_id,
                received_at=received_at or timezone.now(),
            )
    except IntegrityError as error:
        # Only translate the transaction-id collision, not unrelated
        # failures, into a business validation error.
        cause = error.__cause__
        constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
        sqlite_duplicate = str(cause) == "UNIQUE constraint failed: finance_incomingpayment.tenant_id, finance_incomingpayment.external_transaction_id"
        if constraint != "unique_incoming_payment_transaction_per_tenant" and not sqlite_duplicate:
            raise
        replay = IncomingPayment.objects.filter(tenant=tenant, external_transaction_id=external_transaction_id).first()
        if replay is None:
            raise ValidationError("Incoming payment ingestion conflicted with another request") from error
        return _matching_incoming_replay(existing=replay, payment_method=payment_method, amount=amount)
    record_activity(
        tenant=tenant,
        actor=user,
        action="incoming_payment.ingested",
        resource_type="incoming_payment",
        resource_id=str(incoming.id),
    )
    _attempt_auto_match(user=user, tenant=tenant, incoming=incoming)
    return incoming


@transaction.atomic
def match_incoming_payment(*, user, tenant, incoming, student):
    require_permission(user=user, tenant=tenant, permission="finance.reconciliation.match")
    validate_same_tenant(tenant=tenant, incoming=incoming, student=student)
    try:
        locked_incoming = IncomingPayment.objects.select_for_update().get(tenant=tenant, pk=incoming.pk)
    except IncomingPayment.DoesNotExist as error:
        raise ValidationError("Incoming payment is not available in this tenant") from error
    return _complete_match(user=user, tenant=tenant, incoming=locked_incoming, student=student)


@transaction.atomic
def ignore_incoming_payment(*, user, tenant, incoming, reason):
    require_permission(user=user, tenant=tenant, permission="finance.reconciliation.ignore")
    validate_same_tenant(tenant=tenant, incoming=incoming)
    try:
        locked_incoming = IncomingPayment.objects.select_for_update().get(tenant=tenant, pk=incoming.pk)
    except IncomingPayment.DoesNotExist as error:
        raise ValidationError("Incoming payment is not available in this tenant") from error
    if locked_incoming.status != ReconciliationStatus.UNMATCHED:
        raise ValidationError("Incoming payment has already been resolved")
    locked_incoming.status = ReconciliationStatus.IGNORED
    locked_incoming.ignored_reason = reason
    locked_incoming.save(update_fields=["status", "ignored_reason"])
    record_activity(
        tenant=tenant,
        actor=user,
        action="incoming_payment.ignored",
        resource_type="incoming_payment",
        resource_id=str(locked_incoming.id),
    )
    return locked_incoming
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.activity.services import record_activity
from apps.tenancy.services import require_permission

from .models import (
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
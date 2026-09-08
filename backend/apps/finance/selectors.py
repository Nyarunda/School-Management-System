from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate

from .models import LedgerEntryType, Payment, PaymentStatus, StudentLedgerEntry


def student_balance(*, tenant, student):
    entries = StudentLedgerEntry.objects.for_tenant(tenant).filter(student=student)
    debit = entries.filter(entry_type=LedgerEntryType.DEBIT).aggregate(total=Sum("amount"))["total"] or 0
    credit = entries.filter(entry_type=LedgerEntryType.CREDIT).aggregate(total=Sum("amount"))["total"] or 0
    return debit - credit


def _describe_ledger_entry(entry):
    if entry.invoice_id:
        return f"Invoice {entry.invoice.invoice_number}"
    if entry.credit_note_id:
        return f"Credit note {entry.credit_note.credit_note_number}"
    if entry.payment_allocation_id:
        return f"Payment allocation to invoice {entry.payment_allocation.invoice.invoice_number}"
    if entry.allocation_reversal_id:
        return f"Allocation reversal: {entry.allocation_reversal.reason}"
    return ""


def fee_statement_rows(*, tenant, student_id, as_of):
    from apps.students.models import Student

    student = Student.objects.for_tenant(tenant).filter(pk=student_id).first()
    if student is None:
        raise ValidationError("No matching student for this tenant")

    entries = (
        StudentLedgerEntry.objects.for_tenant(tenant)
        .filter(student=student, posted_at__date__lte=as_of)
        .select_related(
            "invoice", "credit_note", "payment_allocation__invoice", "allocation_reversal",
        )
        .order_by("posted_at")
    )
    running_balance = Decimal("0")
    rows = []
    for entry in entries:
        if entry.entry_type == LedgerEntryType.DEBIT:
            debit, credit = entry.amount, Decimal("0")
            running_balance += entry.amount
        else:
            debit, credit = Decimal("0"), entry.amount
            running_balance -= entry.amount
        rows.append({
            "entry_date": entry.posted_at, "description": _describe_ledger_entry(entry),
            "debit": debit, "credit": credit, "running_balance": running_balance,
        })
    return rows


def collections_summary_rows(*, tenant, start_date, end_date):
    payments = (
        Payment.objects.for_tenant(tenant)
        .filter(status=PaymentStatus.RECEIVED, received_at__date__gte=start_date, received_at__date__lte=end_date)
        .annotate(collection_date=TruncDate("received_at"))
        .values("collection_date", "payment_method__name")
        .annotate(payment_count=Count("id"), total_amount=Sum("amount"))
        .order_by("collection_date", "payment_method__name")
    )
    return [
        {
            "collection_date": row["collection_date"], "payment_method": row["payment_method__name"],
            "payment_count": row["payment_count"], "total_amount": row["total_amount"],
        }
        for row in payments
    ]
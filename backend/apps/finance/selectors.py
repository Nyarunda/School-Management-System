from django.db.models import Sum

from .models import LedgerEntryType, StudentLedgerEntry


def student_balance(*, tenant, student):
    entries = StudentLedgerEntry.objects.for_tenant(tenant).filter(student=student)
    debit = entries.filter(entry_type=LedgerEntryType.DEBIT).aggregate(total=Sum("amount"))["total"] or 0
    credit = entries.filter(entry_type=LedgerEntryType.CREDIT).aggregate(total=Sum("amount"))["total"] or 0
    return debit - credit
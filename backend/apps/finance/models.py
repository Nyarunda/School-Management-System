import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.academics.models import AcademicLevel, AcademicYear
from apps.tenancy.models import TenantOwnedModel


class FinanceSetup(TenantOwnedModel):
    currency = models.CharField(max_length=3, default="KES")
    fiscal_year_start_month = models.PositiveSmallIntegerField(default=1)
    configuration = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant"], name="unique_finance_setup_per_tenant")]


class FeeCategory(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=30)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="unique_fee_category_code_per_tenant")]


class FeeItem(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(FeeCategory, on_delete=models.PROTECT, related_name="items")
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=30)
    is_optional = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="unique_fee_item_code_per_tenant")]


class FeeStructure(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="fee_structures")
    academic_level = models.ForeignKey(AcademicLevel, on_delete=models.PROTECT, related_name="fee_structures")
    is_active = models.BooleanField(default=True)
    is_approved = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "name", "academic_year", "academic_level"], name="unique_fee_structure_per_level_year")]


class FeeStructureLine(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fee_structure = models.ForeignKey(FeeStructure, on_delete=models.CASCADE, related_name="lines")
    fee_item = models.ForeignKey(FeeItem, on_delete=models.PROTECT, related_name="structure_lines")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    is_required = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "fee_structure", "fee_item"], name="unique_fee_item_per_structure")]
        indexes = [models.Index(fields=["tenant", "fee_structure"])]


class PaymentMethod(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)
    code = models.CharField(max_length=30)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="unique_payment_method_code_per_tenant")]


class DiscountType(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    fixed_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "code"], name="unique_discount_type_code_per_tenant")]


class NumberSeries(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_type = models.CharField(max_length=80)
    prefix = models.CharField(max_length=20)
    next_value = models.PositiveBigIntegerField(default=1)
    padding = models.PositiveSmallIntegerField(default=6)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "document_type"], name="unique_number_series_per_document_tenant")]

    def preview(self):
        return f"{self.prefix}{self.next_value:0{self.padding}d}"


class FeeAssignmentStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    CANCELLED = "CANCELLED", "Cancelled"


class StudentFeeAssignment(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="fee_assignments")
    fee_structure = models.ForeignKey(FeeStructure, on_delete=models.PROTECT, related_name="student_assignments")
    status = models.CharField(max_length=20, choices=FeeAssignmentStatus.choices, default=FeeAssignmentStatus.ACTIVE)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "student", "fee_structure"],
                name="unique_student_fee_assignment",
            )
        ]


class InvoiceStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ISSUED = "ISSUED", "Issued"
    VOID = "VOID", "Void"


class Invoice(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice_number = models.CharField(max_length=60)
    idempotency_key = models.CharField(max_length=120)
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="invoices")
    assignment = models.OneToOneField(StudentFeeAssignment, on_delete=models.PROTECT, related_name="invoice")
    status = models.CharField(max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.DRAFT)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    issued_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "invoice_number"], name="unique_invoice_number_per_tenant"),
            models.UniqueConstraint(fields=["tenant", "idempotency_key"], name="unique_invoice_idempotency_per_tenant"),
        ]
        indexes = [models.Index(fields=["tenant", "student", "status"])]


class InvoiceLine(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    fee_item = models.ForeignKey(FeeItem, on_delete=models.PROTECT)
    source_fee_structure_line = models.ForeignKey(FeeStructureLine, on_delete=models.PROTECT, null=True, blank=True)
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_amount = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=12, decimal_places=2)


class CreditNoteStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ISSUED = "ISSUED", "Issued"
    VOID = "VOID", "Void"


class CreditNote(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    credit_note_number = models.CharField(max_length=60)
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="credit_notes")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="credit_notes", null=True, blank=True)
    reason = models.CharField(max_length=240)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=CreditNoteStatus.choices, default=CreditNoteStatus.DRAFT)
    issued_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "credit_note_number"], name="unique_credit_note_number_per_tenant")]


class PaymentStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Received"
    REVERSED = "REVERSED", "Reversed"


class Payment(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="payments")
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    external_reference = models.CharField(max_length=120, blank=True, default="")
    idempotency_key = models.CharField(max_length=120)
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.RECEIVED)
    received_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "idempotency_key"], name="unique_payment_idempotency_per_tenant"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
        ]
        indexes = [models.Index(fields=["tenant", "student", "received_at"])]


class PaymentAllocation(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payment_allocations")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    allocated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="payment_allocation_amount_positive")]
        indexes = [models.Index(fields=["tenant", "payment"]), models.Index(fields=["tenant", "invoice"])]


class Receipt(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="receipt")
    receipt_number = models.CharField(max_length=60)
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "receipt_number"], name="unique_receipt_number_per_tenant")]


class PaymentReversal(TenantOwnedModel):
    """Invalidates the payment itself (a bounced cheque, a reversed bank
    transfer, a chargeback) -- distinct from AllocationReversal, which only
    undoes one allocation. Reversing a payment cascades: every active
    allocation on it is reversed via AllocationReversal first, then this
    record marks the payment as no longer available for future allocation.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="reversal")
    reversal_number = models.CharField(max_length=60)
    reason = models.CharField(max_length=240)
    reversed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "reversal_number"], name="unique_payment_reversal_number_per_tenant")]


class AllocationReversal(TenantOwnedModel):
    """Undoes a specific PaymentAllocation (e.g. it was applied to the wrong invoice).

    Distinct from a future PaymentReversal, which will undo the cash itself
    (a bounced cheque, a chargeback) -- that is a different operation with
    different consequences and is deliberately not modeled yet.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    allocation = models.ForeignKey(PaymentAllocation, on_delete=models.PROTECT, related_name="reversals")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=240)
    reversed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="allocation_reversal_amount_positive")]
        indexes = [models.Index(fields=["tenant", "allocation"])]


class LedgerEntryType(models.TextChoices):
    DEBIT = "DEBIT", "Debit"
    CREDIT = "CREDIT", "Credit"


class StudentLedgerEntry(TenantOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="ledger_entries")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, null=True, blank=True, related_name="ledger_entries")
    credit_note = models.ForeignKey(CreditNote, on_delete=models.PROTECT, null=True, blank=True, related_name="ledger_entries")
    payment_allocation = models.ForeignKey(PaymentAllocation, on_delete=models.PROTECT, null=True, blank=True, related_name="ledger_entries")
    allocation_reversal = models.ForeignKey(AllocationReversal, on_delete=models.PROTECT, null=True, blank=True, related_name="ledger_entries")
    entry_type = models.CharField(max_length=10, choices=LedgerEntryType.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    posted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "invoice"], condition=Q(invoice__isnull=False), name="unique_invoice_ledger_entry"),
            models.UniqueConstraint(fields=["tenant", "credit_note"], condition=Q(credit_note__isnull=False), name="unique_credit_note_ledger_entry"),
            models.UniqueConstraint(fields=["tenant", "payment_allocation"], condition=Q(payment_allocation__isnull=False), name="unique_payment_allocation_ledger_entry"),
            models.UniqueConstraint(fields=["tenant", "allocation_reversal"], condition=Q(allocation_reversal__isnull=False), name="unique_allocation_reversal_ledger_entry"),
            models.CheckConstraint(
                condition=Q(invoice__isnull=False) | Q(credit_note__isnull=False) | Q(payment_allocation__isnull=False) | Q(allocation_reversal__isnull=False),
                name="ledger_entry_has_source",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "student", "posted_at"])]


def validate_same_tenant(*, tenant, **objects):
    if any(value.tenant_id != tenant.id for value in objects.values()):
        raise ValidationError("Finance setup records must belong to the same tenant")
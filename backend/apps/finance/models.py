import uuid

from django.core.exceptions import ValidationError
from django.db import models

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


def validate_same_tenant(*, tenant, **objects):
    if any(value.tenant_id != tenant.id for value in objects.values()):
        raise ValidationError("Finance setup records must belong to the same tenant")
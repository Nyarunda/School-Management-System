from django.core.exceptions import ValidationError

from apps.tenancy.services import require_permission

from .models import FeeStructure, FeeStructureLine, validate_same_tenant


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
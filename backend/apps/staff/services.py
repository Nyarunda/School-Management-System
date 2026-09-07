from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.activity.services import record_activity
from apps.tenancy.models import Membership
from apps.tenancy.services import require_permission, require_same_tenant

from .models import Employee, EmployeeDocument, EmployeeQualification, EmploymentStatus

EMPLOYEE_TRANSITIONS = {
    EmploymentStatus.ACTIVE: {EmploymentStatus.SUSPENDED, EmploymentStatus.TERMINATED},
    EmploymentStatus.SUSPENDED: {EmploymentStatus.ACTIVE, EmploymentStatus.TERMINATED},
    EmploymentStatus.TERMINATED: set(),
}

_SQLITE_CONFLICT_MESSAGES = {
    "unique_employee_number_per_tenant": "UNIQUE constraint failed: staff_employee.tenant_id, staff_employee.employee_number",
    "staff_employee_user_account_id_key": "UNIQUE constraint failed: staff_employee.user_account_id",
}

_CONFLICT_MESSAGES = {
    "unique_employee_number_per_tenant": "Employee number already in use for this tenant",
    "staff_employee_user_account_id_key": "This user account is already linked to another employee record",
}


def _translate_conflict(error):
    cause = error.__cause__
    constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
    cause_str = str(cause)
    for name, message in _CONFLICT_MESSAGES.items():
        if constraint == name or cause_str == _SQLITE_CONFLICT_MESSAGES[name]:
            raise ValidationError(message) from error
    raise error


def _require_campus_scope(*, membership, campus_id):
    if membership.campus_id is not None and campus_id != membership.campus_id:
        raise ValidationError("User is not authorized for this campus")


def _validate_dates(date_of_birth, hire_date):
    if date_of_birth is not None and hire_date is not None and date_of_birth >= hire_date:
        raise ValidationError("Date of birth must be before the hire date")


UPDATABLE_FIELDS = [
    "first_name", "last_name", "date_of_birth", "national_id", "phone_number", "email",
    "campus", "department", "job_title", "employment_type", "hire_date",
    "emergency_contact_name", "emergency_contact_phone",
]


def create_employee(
    *, user, tenant, employee_number, first_name, last_name, job_title, employment_type, hire_date,
    campus=None, department="", date_of_birth=None, national_id="", phone_number="", email="",
    emergency_contact_name="", emergency_contact_phone="", actor=None,
):
    membership = require_permission(user=user, tenant=tenant, permission="staff.manage")
    if campus is not None:
        require_same_tenant(tenant=tenant, campus=campus)
    _require_campus_scope(membership=membership, campus_id=campus.id if campus is not None else None)

    employee_number = " ".join(employee_number.strip().upper().split())
    if not employee_number:
        raise ValidationError("Employee number cannot be blank")

    _validate_dates(date_of_birth, hire_date)

    try:
        with transaction.atomic():
            employee = Employee.objects.create(
                tenant=tenant, employee_number=employee_number, first_name=first_name, last_name=last_name,
                date_of_birth=date_of_birth, national_id=national_id, phone_number=phone_number, email=email,
                campus=campus, department=department, job_title=job_title, employment_type=employment_type,
                hire_date=hire_date, emergency_contact_name=emergency_contact_name,
                emergency_contact_phone=emergency_contact_phone,
            )
    except IntegrityError as error:
        _translate_conflict(error)  # always raises
        raise

    record_activity(
        tenant=tenant, actor=actor or user, action="staff.employee.created",
        resource_type="employee", resource_id=str(employee.id),
    )
    return employee


@transaction.atomic
def update_employee_details(*, user, tenant, employee, actor=None, **fields):
    membership = require_permission(user=user, tenant=tenant, permission="staff.manage")
    require_same_tenant(tenant=tenant, employee=employee)

    unknown = set(fields) - set(UPDATABLE_FIELDS)
    if unknown:
        raise ValidationError(f"Unsupported field(s): {', '.join(sorted(unknown))}")

    campus = fields.get("campus")
    if "campus" in fields and campus is not None:
        require_same_tenant(tenant=tenant, campus=campus)

    locked_employee = Employee.objects.select_for_update().get(tenant=tenant, pk=employee.pk)
    _require_campus_scope(membership=membership, campus_id=locked_employee.campus_id)

    if "date_of_birth" in fields or "hire_date" in fields:
        effective_dob = fields.get("date_of_birth", locked_employee.date_of_birth)
        effective_hire = fields.get("hire_date", locked_employee.hire_date)
        _validate_dates(effective_dob, effective_hire)

    if "campus" in fields and locked_employee.user_account_id is not None:
        new_campus_id = campus.id if campus is not None else None
        target_membership = Membership.objects.filter(
            tenant=tenant, user_id=locked_employee.user_account_id, is_active=True,
        ).first()
        if target_membership is not None:
            _require_campus_scope(membership=target_membership, campus_id=new_campus_id)

    previous = {}
    new = {}
    changed_fields = []
    for field, value in fields.items():
        current = getattr(locked_employee, field)
        if field == "campus":
            if current is not None and value is not None and current.id == value.id:
                continue
            if current is None and value is None:
                continue
        elif current == value:
            continue
        previous[field] = str(current) if current is not None else None
        new[field] = str(value) if value is not None else None
        setattr(locked_employee, field, value)
        changed_fields.append(field)

    if not changed_fields:
        return locked_employee

    locked_employee.save(update_fields=changed_fields)
    record_activity(
        tenant=tenant, actor=actor or user, action="staff.employee.updated",
        resource_type="employee", resource_id=str(locked_employee.id),
        metadata={"previous": previous, "new": new},
    )
    return locked_employee


@transaction.atomic
def change_employment_status(*, user, tenant, employee, status, reason="", actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="staff.manage")
    require_same_tenant(tenant=tenant, employee=employee)
    locked_employee = Employee.objects.select_for_update().get(tenant=tenant, pk=employee.pk)
    _require_campus_scope(membership=membership, campus_id=locked_employee.campus_id)

    if status not in EMPLOYEE_TRANSITIONS.get(locked_employee.status, set()):
        raise ValidationError(f"Cannot move employee from {locked_employee.status} to {status}")

    locked_employee.status = status
    locked_employee.save(update_fields=["status"])
    record_activity(
        tenant=tenant, actor=actor or user, action=f"staff.employee.{status.lower()}",
        resource_type="employee", resource_id=str(locked_employee.id),
        metadata={"reason": reason} if reason else {},
    )
    return locked_employee


def add_employee_document(*, user, tenant, employee, document_type, file_name, actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="staff.manage")
    require_same_tenant(tenant=tenant, employee=employee)
    _require_campus_scope(membership=membership, campus_id=employee.campus_id)

    document = EmployeeDocument.objects.create(
        tenant=tenant, employee=employee, document_type=document_type, file_name=file_name,
    )
    record_activity(
        tenant=tenant, actor=actor or user, action="staff.document_added",
        resource_type="employee", resource_id=str(employee.id),
        metadata={"document_id": str(document.id)},
    )
    return document


def add_employee_qualification(*, user, tenant, employee, title, institution="", year_obtained=None, actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="staff.manage")
    require_same_tenant(tenant=tenant, employee=employee)
    _require_campus_scope(membership=membership, campus_id=employee.campus_id)

    if year_obtained is not None:
        current_year = timezone.now().year
        if not (1900 <= year_obtained <= current_year):
            raise ValidationError(f"year_obtained must be between 1900 and {current_year}")

    qualification = EmployeeQualification.objects.create(
        tenant=tenant, employee=employee, title=title, institution=institution, year_obtained=year_obtained,
    )
    record_activity(
        tenant=tenant, actor=actor or user, action="staff.qualification_added",
        resource_type="employee", resource_id=str(employee.id),
        metadata={"qualification_id": str(qualification.id)},
    )
    return qualification


@transaction.atomic
def link_user_account(*, user, tenant, employee, user_account, actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="staff.user_link.manage")
    require_same_tenant(tenant=tenant, employee=employee)
    locked_employee = Employee.objects.select_for_update().get(tenant=tenant, pk=employee.pk)
    _require_campus_scope(membership=membership, campus_id=locked_employee.campus_id)

    if locked_employee.user_account_id == user_account.id:
        return locked_employee

    if locked_employee.user_account_id is not None:
        raise ValidationError("Employee is already linked to a different user account; unlink it first")

    target_membership = Membership.objects.filter(tenant=tenant, user=user_account, is_active=True).first()
    if target_membership is None:
        raise ValidationError("User must have an active membership in this tenant before being linked")
    _require_campus_scope(membership=target_membership, campus_id=locked_employee.campus_id)

    locked_employee.user_account = user_account
    try:
        locked_employee.save(update_fields=["user_account"])
    except IntegrityError as error:
        _translate_conflict(error)  # always raises
        raise

    record_activity(
        tenant=tenant, actor=actor or user, action="staff.user_linked",
        resource_type="employee", resource_id=str(locked_employee.id),
        metadata={"user_id": str(user_account.id)},
    )
    return locked_employee


@transaction.atomic
def unlink_user_account(*, user, tenant, employee, actor=None):
    membership = require_permission(user=user, tenant=tenant, permission="staff.user_link.manage")
    require_same_tenant(tenant=tenant, employee=employee)
    locked_employee = Employee.objects.select_for_update().get(tenant=tenant, pk=employee.pk)
    _require_campus_scope(membership=membership, campus_id=locked_employee.campus_id)

    if locked_employee.user_account_id is None:
        return locked_employee

    previous_user_id = str(locked_employee.user_account_id)
    locked_employee.user_account = None
    locked_employee.save(update_fields=["user_account"])
    record_activity(
        tenant=tenant, actor=actor or user, action="staff.user_unlinked",
        resource_type="employee", resource_id=str(locked_employee.id),
        metadata={"user_id": previous_user_id},
    )
    return locked_employee

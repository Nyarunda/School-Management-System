from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

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
    require_permission(user=user, tenant=tenant, permission="staff.manage")
    if campus is not None:
        require_same_tenant(tenant=tenant, campus=campus)

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


def update_employee_details(*, user, tenant, employee, actor=None, **fields):
    require_permission(user=user, tenant=tenant, permission="staff.manage")
    require_same_tenant(tenant=tenant, employee=employee)

    unknown = set(fields) - set(UPDATABLE_FIELDS)
    if unknown:
        raise ValidationError(f"Unsupported field(s): {', '.join(sorted(unknown))}")

    campus = fields.get("campus")
    if "campus" in fields and campus is not None:
        require_same_tenant(tenant=tenant, campus=campus)

    previous = {}
    new = {}
    changed_fields = []
    for field, value in fields.items():
        current = getattr(employee, field)
        if field == "campus":
            if current is not None and value is not None and current.id == value.id:
                continue
            if current is None and value is None:
                continue
        elif current == value:
            continue
        previous[field] = str(current) if current is not None else None
        new[field] = str(value) if value is not None else None
        setattr(employee, field, value)
        changed_fields.append(field)

    if not changed_fields:
        return employee

    employee.save(update_fields=changed_fields)
    record_activity(
        tenant=tenant, actor=actor or user, action="staff.employee.updated",
        resource_type="employee", resource_id=str(employee.id),
        metadata={"previous": previous, "new": new},
    )
    return employee


def change_employment_status(*, user, tenant, employee, status, reason="", actor=None):
    require_permission(user=user, tenant=tenant, permission="staff.manage")
    require_same_tenant(tenant=tenant, employee=employee)

    if status not in EMPLOYEE_TRANSITIONS.get(employee.status, set()):
        raise ValidationError(f"Cannot move employee from {employee.status} to {status}")

    employee.status = status
    employee.save(update_fields=["status"])
    record_activity(
        tenant=tenant, actor=actor or user, action=f"staff.employee.{status.lower()}",
        resource_type="employee", resource_id=str(employee.id),
        metadata={"reason": reason} if reason else {},
    )
    return employee


def add_employee_document(*, user, tenant, employee, document_type, file_name, actor=None):
    require_permission(user=user, tenant=tenant, permission="staff.manage")
    require_same_tenant(tenant=tenant, employee=employee)

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
    require_permission(user=user, tenant=tenant, permission="staff.manage")
    require_same_tenant(tenant=tenant, employee=employee)

    qualification = EmployeeQualification.objects.create(
        tenant=tenant, employee=employee, title=title, institution=institution, year_obtained=year_obtained,
    )
    record_activity(
        tenant=tenant, actor=actor or user, action="staff.qualification_added",
        resource_type="employee", resource_id=str(employee.id),
        metadata={"qualification_id": str(qualification.id)},
    )
    return qualification


def link_user_account(*, user, tenant, employee, user_account, actor=None):
    require_permission(user=user, tenant=tenant, permission="staff.user_link.manage")
    require_same_tenant(tenant=tenant, employee=employee)

    if employee.user_account_id == user_account.id:
        return employee

    if employee.user_account_id is not None:
        raise ValidationError("Employee is already linked to a different user account; unlink it first")

    has_membership = Membership.objects.filter(tenant=tenant, user=user_account, is_active=True).exists()
    if not has_membership:
        raise ValidationError("User must have an active membership in this tenant before being linked")

    employee.user_account = user_account
    try:
        with transaction.atomic():
            employee.save(update_fields=["user_account"])
    except IntegrityError as error:
        _translate_conflict(error)  # always raises
        raise

    record_activity(
        tenant=tenant, actor=actor or user, action="staff.user_linked",
        resource_type="employee", resource_id=str(employee.id),
        metadata={"user_id": str(user_account.id)},
    )
    return employee


def unlink_user_account(*, user, tenant, employee, actor=None):
    require_permission(user=user, tenant=tenant, permission="staff.user_link.manage")
    require_same_tenant(tenant=tenant, employee=employee)

    if employee.user_account_id is None:
        return employee

    previous_user_id = str(employee.user_account_id)
    employee.user_account = None
    employee.save(update_fields=["user_account"])
    record_activity(
        tenant=tenant, actor=actor or user, action="staff.user_unlinked",
        resource_type="employee", resource_id=str(employee.id),
        metadata={"user_id": previous_user_id},
    )
    return employee

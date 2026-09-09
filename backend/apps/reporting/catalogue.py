import uuid as uuid_module
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.academics.models import ClassGroup
from apps.assessments import selectors as assessments_selectors
from apps.attendance import selectors as attendance_selectors
from apps.finance import selectors as finance_selectors
from apps.staff import selectors as staff_selectors
from apps.students import selectors as students_selectors


def _coerce_uuid(value):
    try:
        return str(uuid_module.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise ValidationError("Must be a valid UUID")


def _coerce_date(value):
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ValidationError("Must be a date in YYYY-MM-DD format")


def _coerce_string(value):
    return str(value)


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError("Must be an integer")


_COERCERS = {"uuid": _coerce_uuid, "date": _coerce_date, "string": _coerce_string, "int": _coerce_int}


def require_campus_scope(*, membership, params):
    """Same guard already duplicated per-app as `_require_campus_scope` in
    timetable/assessments/attendance/leave/staff services.py: a user whose
    Membership.campus is set can never read a *different* campus's data,
    even with the right permission. Reporting's campus-filterable reports
    must not become a way around that -- this mirrors the existing
    convention of one small copy per app rather than a shared tenancy
    utility, since that's what the other five copies already establish.
    """
    campus_id = params.get("campus_id")
    if campus_id and membership.campus_id is not None and str(membership.campus_id) != str(campus_id):
        raise ValidationError("User is not authorized for this campus")


def require_assessment_class_campus_scope(*, membership, params):
    """RC Area 3: assessments.results_sheet had no authorize hook at all,
    even though the underlying Assessments domain enforces campus scope on
    the same class_group (assessments.services._require_assessment_campus_scope,
    added this same area) -- without this, a tenant-wide
    reports.assessments.export holder could pull any class's results,
    bypassing the scope that gates direct access to the same data. Same
    semantics as require_campus_scope above; resolves the campus from
    class_group_id since this report has no campus_id param of its own.
    Not-found and wrong-campus are deliberately indistinguishable to the
    caller (both raise the same message) -- anti-enumeration, matching
    resolve_tenant_object's get_object_or_404 shape elsewhere.
    """
    if membership.campus_id is None:
        return
    class_group = ClassGroup.objects.filter(
        tenant_id=membership.tenant_id, pk=params["class_group_id"],
    ).only("campus_id").first()
    if class_group is None or class_group.campus_id != membership.campus_id:
        raise ValidationError("User is not authorized for this campus")


@dataclass(frozen=True)
class ReportParameter:
    type: str  # "uuid" | "date" | "string"
    required: bool = False
    default: Optional[Callable] = None


@dataclass(frozen=True)
class ReportDefinition:
    label: str
    permission_group: str
    module_code: str  # Milestone 21: apps.platform module this report's data belongs to
    parameters: dict
    columns: list  # [(field_name, header), ...] in export/preview order
    query: Callable  # (*, tenant, limit=None, **params) -> iterable of dict-like rows
    max_rows: int  # hard cap enforced identically by preview and export
    max_date_range_days: Optional[int] = None
    date_range_fields: Optional[tuple] = None  # (start_field_name, end_field_name)
    # (*, membership, params) -> None, raises ValidationError to reject.
    # Re-run both at request time and (via generate_report_export) at
    # generation time, so a requester who loses access between requesting
    # and a delayed async generation can't still receive the file.
    authorize: Optional[Callable] = None


REPORT_CATALOGUE = {
    "finance.fee_statement": ReportDefinition(
        label="Student Fee Statement", permission_group="finance", module_code="finance",
        parameters={
            "student_id": ReportParameter(type="uuid", required=True),
            "as_of": ReportParameter(type="date", required=False, default=lambda: timezone.now().date()),
        },
        columns=[
            ("entry_date", "Date"), ("description", "Description"),
            ("debit", "Debit"), ("credit", "Credit"), ("running_balance", "Balance"),
        ],
        query=finance_selectors.fee_statement_rows, max_rows=5000,
    ),
    "finance.collections_summary": ReportDefinition(
        label="Collections Summary", permission_group="finance", module_code="finance",
        parameters={
            "start_date": ReportParameter(type="date", required=True),
            "end_date": ReportParameter(type="date", required=True),
        },
        columns=[
            ("collection_date", "Date"), ("payment_method", "Method"),
            ("payment_count", "Payments"), ("total_amount", "Total Amount"),
        ],
        query=finance_selectors.collections_summary_rows, max_rows=5000,
        max_date_range_days=366, date_range_fields=("start_date", "end_date"),
    ),
    "students.enrollment_register": ReportDefinition(
        label="Enrollment Register", permission_group="students", module_code="student_records",
        parameters={
            "campus_id": ReportParameter(type="int", required=False),  # Campus.id is a plain integer PK, not a UUID
            "status": ReportParameter(type="string", required=False),
        },
        columns=[
            ("admission_number", "Admission No."), ("full_name", "Name"), ("status", "Status"),
            ("campus", "Campus"), ("date_of_birth", "Date of Birth"),
        ],
        query=students_selectors.enrollment_register_rows, max_rows=20000,
        authorize=require_campus_scope,
    ),
    "attendance.absence_summary": ReportDefinition(
        label="Absence Summary", permission_group="attendance", module_code="attendance",
        parameters={
            "start_date": ReportParameter(type="date", required=True),
            "end_date": ReportParameter(type="date", required=True),
            "campus_id": ReportParameter(type="int", required=False),  # Campus.id is a plain integer PK, not a UUID
        },
        columns=[
            ("admission_number", "Admission No."), ("full_name", "Name"),
            ("total_sessions", "Total Sessions"), ("absent_count", "Absences"), ("late_count", "Late"),
        ],
        query=attendance_selectors.absence_summary_rows, max_rows=20000,
        max_date_range_days=366, date_range_fields=("start_date", "end_date"),
        authorize=require_campus_scope,
    ),
    "assessments.results_sheet": ReportDefinition(
        label="Results Sheet", permission_group="assessments", module_code="assessments",
        parameters={
            "class_group_id": ReportParameter(type="uuid", required=True),
            "term_id": ReportParameter(type="uuid", required=True),
        },
        columns=[
            ("admission_number", "Admission No."), ("full_name", "Name"), ("subject", "Subject"),
            ("assessment", "Assessment"), ("mark_status", "Status"), ("score", "Score"), ("grade", "Grade"),
        ],
        query=assessments_selectors.results_sheet_rows, max_rows=20000,
        authorize=require_assessment_class_campus_scope,
    ),
    "staff.employee_register": ReportDefinition(
        label="Employee Register", permission_group="staff", module_code="staff_hr",
        parameters={
            "campus_id": ReportParameter(type="int", required=False),  # Campus.id is a plain integer PK, not a UUID
            "status": ReportParameter(type="string", required=False),
        },
        columns=[
            ("employee_number", "Employee No."), ("full_name", "Name"), ("job_title", "Job Title"),
            ("department", "Department"), ("employment_type", "Employment Type"), ("status", "Status"),
            ("campus", "Campus"), ("hire_date", "Hire Date"),
        ],
        query=staff_selectors.employee_register_rows, max_rows=20000,
        authorize=require_campus_scope,
    ),
}


def get_report_definition(report_code):
    definition = REPORT_CATALOGUE.get(report_code)
    if definition is None:
        raise ValidationError(f"Unknown report code: {report_code}")
    return definition


def validate_and_coerce_params(*, definition, raw_params):
    """The single enforcement point for parameter shape and bounds, used
    identically by run_report_preview and generate_report_export so the
    two paths can never drift out of sync on what's a valid/bounded
    request.
    """
    coerced = {}
    for name, spec in definition.parameters.items():
        raw_value = raw_params.get(name)
        if raw_value in (None, ""):
            if spec.required:
                raise ValidationError(f"Missing required parameter: {name}")
            coerced[name] = spec.default() if spec.default is not None else None
            continue
        coerced[name] = _COERCERS[spec.type](raw_value)

    unknown = set(raw_params) - set(definition.parameters)
    if unknown:
        raise ValidationError(f"Unknown parameter(s): {', '.join(sorted(unknown))}")

    if definition.max_date_range_days is not None and definition.date_range_fields:
        start_field, end_field = definition.date_range_fields
        start, end = coerced[start_field], coerced[end_field]
        if end < start:
            raise ValidationError(f"{end_field} must not be before {start_field}")
        if (end - start).days > definition.max_date_range_days:
            raise ValidationError(f"Date range cannot exceed {definition.max_date_range_days} days")

    return coerced

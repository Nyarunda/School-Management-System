import uuid as uuid_module
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

from django.core.exceptions import ValidationError
from django.utils import timezone

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


_COERCERS = {"uuid": _coerce_uuid, "date": _coerce_date, "string": _coerce_string}


@dataclass(frozen=True)
class ReportParameter:
    type: str  # "uuid" | "date" | "string"
    required: bool = False
    default: Optional[Callable] = None


@dataclass(frozen=True)
class ReportDefinition:
    label: str
    permission_group: str
    parameters: dict
    columns: list  # [(field_name, header), ...] in export/preview order
    query: Callable  # (*, tenant, **params) -> iterable of dict-like rows
    max_rows: int  # hard cap enforced identically by preview and export
    max_date_range_days: Optional[int] = None
    date_range_fields: Optional[tuple] = None  # (start_field_name, end_field_name)


REPORT_CATALOGUE = {
    "finance.fee_statement": ReportDefinition(
        label="Student Fee Statement", permission_group="finance",
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
        label="Collections Summary", permission_group="finance",
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
        label="Enrollment Register", permission_group="students",
        parameters={
            "campus_id": ReportParameter(type="uuid", required=False),
            "status": ReportParameter(type="string", required=False),
        },
        columns=[
            ("admission_number", "Admission No."), ("full_name", "Name"), ("status", "Status"),
            ("campus", "Campus"), ("date_of_birth", "Date of Birth"),
        ],
        query=students_selectors.enrollment_register_rows, max_rows=20000,
    ),
    "attendance.absence_summary": ReportDefinition(
        label="Absence Summary", permission_group="attendance",
        parameters={
            "start_date": ReportParameter(type="date", required=True),
            "end_date": ReportParameter(type="date", required=True),
            "campus_id": ReportParameter(type="uuid", required=False),
        },
        columns=[
            ("admission_number", "Admission No."), ("full_name", "Name"),
            ("total_sessions", "Total Sessions"), ("absent_count", "Absences"), ("late_count", "Late"),
        ],
        query=attendance_selectors.absence_summary_rows, max_rows=20000,
        max_date_range_days=366, date_range_fields=("start_date", "end_date"),
    ),
    "assessments.results_sheet": ReportDefinition(
        label="Results Sheet", permission_group="assessments",
        parameters={
            "class_group_id": ReportParameter(type="uuid", required=True),
            "term_id": ReportParameter(type="uuid", required=True),
        },
        columns=[
            ("admission_number", "Admission No."), ("full_name", "Name"), ("subject", "Subject"),
            ("assessment", "Assessment"), ("mark_status", "Status"), ("score", "Score"), ("grade", "Grade"),
        ],
        query=assessments_selectors.results_sheet_rows, max_rows=20000,
    ),
    "staff.employee_register": ReportDefinition(
        label="Employee Register", permission_group="staff",
        parameters={
            "campus_id": ReportParameter(type="uuid", required=False),
            "status": ReportParameter(type="string", required=False),
        },
        columns=[
            ("employee_number", "Employee No."), ("full_name", "Name"), ("job_title", "Job Title"),
            ("department", "Department"), ("employment_type", "Employment Type"), ("status", "Status"),
            ("campus", "Campus"), ("hire_date", "Hire Date"),
        ],
        query=staff_selectors.employee_register_rows, max_rows=20000,
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

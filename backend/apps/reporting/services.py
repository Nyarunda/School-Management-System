import csv
import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.services import upload_document
from apps.tenancy.services import require_permission

from .catalogue import get_report_definition, validate_and_coerce_params
from .models import ReportExportJob

REPORT_EXPORT_RETENTION_DAYS = 7  # fixed platform policy, not a tenant setup choice -- exports are derived/transient
MAX_PREVIEW_PAGE_SIZE = 100


def _permission_for(definition, capability):
    return f"reports.{definition.permission_group}.{capability}"


def _serialize_params(coerced):
    """JSONField-safe: dates become ISO strings; everything else round-trips
    as-is. validate_and_coerce_params' date coercer accepts either shape,
    so re-validating a job's frozen params later is symmetric with
    validating the original request.
    """
    serialized = {}
    for key, value in coerced.items():
        serialized[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return serialized


def run_report_preview(*, user, tenant, report_code, params, page=1, page_size=25):
    definition = get_report_definition(report_code)
    require_permission(user=user, tenant=tenant, permission=_permission_for(definition, "view"))
    coerced = validate_and_coerce_params(definition=definition, raw_params=params)
    rows = list(definition.query(tenant=tenant, **coerced))
    page_size = max(1, min(page_size, MAX_PREVIEW_PAGE_SIZE))
    page = max(1, page)
    start = (page - 1) * page_size
    return {"columns": definition.columns, "rows": rows[start:start + page_size], "total_count": len(rows)}


def request_report_export(*, user, tenant, report_code, params):
    """Only ever creates the durable job row -- no query execution happens
    here, mirroring publish_notification_event's post-18.1 discipline so a
    slow or misconfigured report can never block the requesting HTTP
    request.
    """
    definition = get_report_definition(report_code)
    require_permission(user=user, tenant=tenant, permission=_permission_for(definition, "export"))
    coerced = validate_and_coerce_params(definition=definition, raw_params=params)
    return ReportExportJob.objects.create(
        tenant=tenant, report_code=report_code, params=_serialize_params(coerced), requested_by=user,
    )


def generate_report_export(*, job):
    """Async expansion body -- re-validates params as defense in depth
    (the job's params were already validated once at request time, but
    this is the actual security/bounds boundary since it's what touches
    the database and the filesystem).
    """
    definition = get_report_definition(job.report_code)
    coerced = validate_and_coerce_params(definition=definition, raw_params=job.params)
    rows = list(definition.query(tenant=job.tenant, **coerced))
    if len(rows) > definition.max_rows:
        raise ValidationError(
            f"Report has {len(rows)} rows, exceeding the maximum of {definition.max_rows}. "
            "Narrow the parameters and try again."
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header for _, header in definition.columns])
    for row in rows:
        writer.writerow([row.get(field, "") for field, _ in definition.columns])
    content = buffer.getvalue().encode("utf-8")

    file_obj = SimpleUploadedFile(f"{job.report_code}.csv", content, content_type="text/csv")
    document = upload_document(
        tenant=job.tenant, uploaded_by=job.requested_by, file_obj=file_obj,
        original_filename=f"{job.report_code}-{job.id}.csv", content_type="text/csv",
        retention_days=REPORT_EXPORT_RETENTION_DAYS,
    )
    job.document = document
    job.row_count = len(rows)
    job.save(update_fields=["document", "row_count"])

import csv
import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError

from apps.activity.services import record_activity
from apps.documents.services import upload_document
from apps.tenancy.services import require_permission

from .catalogue import get_report_definition, validate_and_coerce_params
from .models import ReportExportJob

REPORT_EXPORT_RETENTION_DAYS = 7  # fixed platform policy, not a tenant setup choice -- exports are derived/transient
MAX_PREVIEW_PAGE_SIZE = 100
# Preview is a quick look, not a data-browsing tool -- that's what export is
# for. Requesting a page beyond this depth is rejected outright rather than
# silently allowed to crawl through the same offset-pagination cost export
# was designed to avoid.
MAX_PREVIEW_ROWS = 1000
# Cells starting with any of these can be interpreted as a formula by
# Excel/LibreOffice when the CSV is opened -- neutralized by prefixing with
# a leading quote, centrally, so no individual selector has to know about it.
_DANGEROUS_CSV_PREFIXES = ("=", "+", "-", "@")


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


def _sanitize_csv_cell(value):
    text = "" if value is None else str(value)
    if text and text[0] in _DANGEROUS_CSV_PREFIXES:
        return "'" + text
    return text


def _write_csv(*, columns, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header for _, header in columns])
    for row in rows:
        writer.writerow([_sanitize_csv_cell(row.get(field, "")) for field, _ in columns])
    return buffer.getvalue().encode("utf-8")


def run_report_preview(*, user, tenant, report_code, params, page=1, page_size=25):
    definition = get_report_definition(report_code)
    membership = require_permission(user=user, tenant=tenant, permission=_permission_for(definition, "view"))
    coerced = validate_and_coerce_params(definition=definition, raw_params=params)
    if definition.authorize is not None:
        definition.authorize(membership=membership, params=coerced)

    page_size = max(1, min(page_size, MAX_PREVIEW_PAGE_SIZE))
    page = max(1, page)
    start = (page - 1) * page_size
    if start >= MAX_PREVIEW_ROWS:
        raise ValidationError(f"Preview is limited to the first {MAX_PREVIEW_ROWS} rows; use export for the full dataset")
    fetch_limit = min(start + page_size + 1, MAX_PREVIEW_ROWS + 1)
    rows = list(definition.query(tenant=tenant, limit=fetch_limit, **coerced))
    page_rows = rows[start:start + page_size]
    has_more = len(rows) > start + page_size
    return {"columns": definition.columns, "rows": page_rows, "has_more": has_more}


def request_report_export(*, user, tenant, report_code, params, idempotency_key=None):
    """Only ever creates the durable job row -- no query execution happens
    here, mirroring publish_notification_event's post-18.1 discipline so a
    slow or misconfigured report can never block the requesting HTTP
    request.

    idempotency_key is optional -- a client that supplies one gets the same
    job back on a repeat request (e.g. a double-click), following the exact
    replay/race pattern already proven by
    apps.notifications.services.publish_notification_event. Omitting it
    preserves the original always-create behavior.
    """
    definition = get_report_definition(report_code)
    membership = require_permission(user=user, tenant=tenant, permission=_permission_for(definition, "export"))
    coerced = validate_and_coerce_params(definition=definition, raw_params=params)
    if definition.authorize is not None:
        definition.authorize(membership=membership, params=coerced)

    serialized = _serialize_params(coerced)
    key = idempotency_key or ""
    if key:
        existing = ReportExportJob.objects.filter(tenant=tenant, idempotency_key=key).first()
        if existing is not None:
            job = _matching_export_replay(existing=existing, report_code=report_code, params=serialized)
            _record_export_requested(tenant=tenant, user=user, job=job)
            return job
    try:
        job = ReportExportJob.objects.create(
            tenant=tenant, report_code=report_code, params=serialized, requested_by=user, idempotency_key=key,
        )
    except IntegrityError:
        if not key:
            raise
        replay = ReportExportJob.objects.filter(tenant=tenant, idempotency_key=key).first()
        if replay is None:
            raise
        job = _matching_export_replay(existing=replay, report_code=report_code, params=serialized)
    _record_export_requested(tenant=tenant, user=user, job=job)
    return job


def _record_export_requested(*, tenant, user, job):
    """Audits the security-relevant action (a user requested this export),
    not job generation (a worker/internal event) -- see generate_report_export.
    Bounded, non-sensitive metadata only: never the report's own params
    (student_id, campus filters, date ranges, etc. can themselves be
    sensitive).
    """
    record_activity(
        tenant=tenant, actor=user, action="report.export.requested",
        resource_type="report_export_job", resource_id=str(job.id),
        metadata={"job_id": str(job.id), "report_code": job.report_code},
    )


def _matching_export_replay(*, existing, report_code, params):
    if (existing.report_code, existing.params) != (report_code, params):
        raise ValidationError("Idempotency key already used with a different report request")
    return existing


def generate_report_export(*, job):
    """Async expansion body. Re-validates params as defense in depth (the
    job's params were already validated once at request time, but this is
    the actual security/bounds boundary since it's what touches the database
    and the filesystem), and re-checks permission/membership/scope against
    the requester -- a job can sit PENDING for a while (retries, backlog),
    long enough for the requester to lose access in the meantime, so
    request-time authorization alone isn't enough. A requester who no longer
    exists fails closed rather than generating anyway.
    """
    definition = get_report_definition(job.report_code)
    if job.requested_by_id is None:
        raise ValidationError("The requesting user is no longer available; export cannot be generated")
    membership = require_permission(
        user=job.requested_by, tenant=job.tenant, permission=_permission_for(definition, "export"),
    )
    coerced = validate_and_coerce_params(definition=definition, raw_params=job.params)
    if definition.authorize is not None:
        definition.authorize(membership=membership, params=coerced)

    rows = list(definition.query(tenant=job.tenant, limit=definition.max_rows + 1, **coerced))
    if len(rows) > definition.max_rows:
        raise ValidationError(
            f"Report has more than {definition.max_rows} rows. Narrow the parameters and try again."
        )

    content = _write_csv(columns=definition.columns, rows=rows)
    file_obj = SimpleUploadedFile(f"{job.report_code}.csv", content, content_type="text/csv")
    document = upload_document(
        tenant=job.tenant, uploaded_by=job.requested_by, file_obj=file_obj,
        original_filename=f"{job.report_code}-{job.id}.csv", content_type="text/csv",
        retention_days=REPORT_EXPORT_RETENTION_DAYS,
    )
    job.document = document
    job.row_count = len(rows)
    job.save(update_fields=["document", "row_count"])

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.documents.services import open_document_stream
from apps.tenancy.services import require_membership, require_permission

from .catalogue import REPORT_CATALOGUE, get_report_definition
from .models import ReportExportJob
from .services import request_report_export, run_report_preview


def resolve_reports_membership(request):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        return require_membership(user=request.user, tenant_slug=slug)
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error


def resolve_reports_tenant(request, *, definition, capability):
    """Resolves both tenant membership and the report-specific permission
    up front, so an authorization failure surfaces as 403 here rather than
    falling through to the service's own (defense-in-depth) permission
    check, which -- like every other app's services in this codebase --
    only raises a generic ValidationError, not a DRF PermissionDenied.
    """
    tenant = resolve_reports_membership(request).tenant
    try:
        require_permission(user=request.user, tenant=tenant, permission=f"reports.{definition.permission_group}.{capability}")
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error
    return tenant


def resolve_job(tenant, job_id):
    try:
        return get_object_or_404(ReportExportJob.objects.filter(tenant=tenant), pk=job_id)
    except DjangoValidationError as error:
        raise NotFound("No matching record for the given identifier") from error


def api_validation_error(error):
    return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)


class ReportCatalogueListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        membership = resolve_reports_membership(request)
        permissions = set(membership.role.permissions)
        is_superuser = request.user.is_superuser
        visible = []
        for code, definition in REPORT_CATALOGUE.items():
            can_view = is_superuser or f"reports.{definition.permission_group}.view" in permissions
            can_export = is_superuser or f"reports.{definition.permission_group}.export" in permissions
            if not (can_view or can_export):
                continue
            visible.append({
                "code": code, "label": definition.label, "permission_group": definition.permission_group,
                "parameters": {
                    name: {"type": spec.type, "required": spec.required} for name, spec in definition.parameters.items()
                },
                "can_view": can_view, "can_export": can_export,
            })
        return Response(visible)


class ReportPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, report_code):
        try:
            definition = get_report_definition(report_code)
        except DjangoValidationError as error:
            return api_validation_error(error)
        tenant = resolve_reports_tenant(request, definition=definition, capability="view")
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 25))
        params = {key: value for key, value in request.query_params.items() if key not in ("page", "page_size")}
        try:
            result = run_report_preview(
                user=request.user, tenant=tenant, report_code=report_code, params=params, page=page, page_size=page_size,
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(result)


class ReportExportJobSerializer(serializers.ModelSerializer):
    download_available = serializers.SerializerMethodField()

    class Meta:
        model = ReportExportJob
        fields = ["id", "report_code", "status", "row_count", "created_at", "processed_at", "last_error", "download_available"]
        read_only_fields = fields

    def get_download_available(self, job):
        return job.document_id is not None


class ReportExportRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, report_code):
        try:
            definition = get_report_definition(report_code)
        except DjangoValidationError as error:
            return api_validation_error(error)
        tenant = resolve_reports_tenant(request, definition=definition, capability="export")
        try:
            job = request_report_export(user=request.user, tenant=tenant, report_code=report_code, params=request.data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(ReportExportJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class ReportExportJobDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        tenant = resolve_reports_membership(request).tenant
        job = resolve_job(tenant, job_id)
        return Response(ReportExportJobSerializer(job).data)


class ReportExportDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        # Membership only, to resolve the job first -- the job's own
        # report_code determines which permission actually gates the
        # download, checked once the job (and therefore its definition)
        # is known.
        tenant = resolve_reports_membership(request).tenant
        job = resolve_job(tenant, job_id)
        definition = get_report_definition(job.report_code)
        try:
            require_permission(user=request.user, tenant=tenant, permission=f"reports.{definition.permission_group}.export")
        except DjangoValidationError as error:
            raise PermissionDenied(error.messages) from error
        if job.document is None:
            raise NotFound("This export is not ready yet")
        stream = open_document_stream(document=job.document)
        response = FileResponse(stream, content_type="text/csv")
        safe_name = job.document.original_filename.replace('"', "")
        response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
        return response

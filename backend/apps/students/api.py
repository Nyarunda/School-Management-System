from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.documents.services import open_document_stream
from apps.platform.services import require_module_enabled
from apps.tenancy.services import require_permission

from .models import Student, StudentDocument
from .selectors import list_students
from .services import add_student_document, delete_student_document


class StudentListPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class StudentListSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    admission_number = serializers.CharField()
    full_name = serializers.SerializerMethodField()
    status = serializers.CharField()
    campus = serializers.CharField(source="campus.name", allow_null=True)

    def get_full_name(self, student):
        return student.full_name


def resolve_request_tenant(request, permission="students.view"):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        membership = require_permission(
            user=request.user, tenant_slug=slug, permission=permission
        )
        require_module_enabled(tenant=membership.tenant, module_code="student_records")
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error
    return membership.tenant


def resolve_tenant_object(queryset, pk):
    try:
        return get_object_or_404(queryset, pk=pk)
    except DjangoValidationError as error:
        raise NotFound("No matching record for the given identifier") from error


def api_validation_error(error):
    return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)


class StudentListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = StudentListPagination
    serializer_class = StudentListSerializer

    def get_queryset(self):
        return list_students(tenant=resolve_request_tenant(self.request))


# --- Documents ---------------------------------------------------------

class StudentDocumentSerializer(serializers.ModelSerializer):
    original_filename = serializers.CharField(source="document.original_filename", read_only=True, default=None)
    content_type = serializers.CharField(source="document.content_type", read_only=True, default=None)
    size_bytes = serializers.IntegerField(source="document.size_bytes", read_only=True, default=None)
    uploaded_at = serializers.DateTimeField(source="document.created_at", read_only=True, default=None)

    class Meta:
        model = StudentDocument
        fields = ["id", "document_type", "original_filename", "content_type", "size_bytes", "uploaded_at"]
        read_only_fields = fields


class StudentDocumentUploadSerializer(serializers.Serializer):
    document_type = serializers.CharField(max_length=80)
    file = serializers.FileField()


class StudentDocumentListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = StudentDocumentSerializer
    pagination_class = StudentListPagination
    parser_classes = [MultiPartParser]

    def get_student(self):
        tenant = resolve_request_tenant(self.request, "students.document.view")
        return tenant, resolve_tenant_object(Student.objects.filter(tenant=tenant), self.kwargs["student_id"])

    def get_queryset(self):
        _, student = self.get_student()
        return StudentDocument.objects.filter(student=student).select_related("document").order_by("-document__created_at")

    def create(self, request, *args, **kwargs):
        tenant = resolve_request_tenant(request, "students.document.manage")
        student = resolve_tenant_object(Student.objects.filter(tenant=tenant), self.kwargs["student_id"])
        serializer = StudentDocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded_file = serializer.validated_data["file"]
        try:
            document = add_student_document(
                user=request.user, tenant=tenant, student=student,
                document_type=serializer.validated_data["document_type"], file_obj=uploaded_file,
                original_filename=uploaded_file.name, content_type=uploaded_file.content_type,
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(StudentDocumentSerializer(document).data, status=status.HTTP_201_CREATED)


class StudentDocumentDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, student_id, document_id):
        tenant = resolve_request_tenant(request, "students.document.view")
        student = resolve_tenant_object(Student.objects.filter(tenant=tenant), student_id)
        student_document = resolve_tenant_object(StudentDocument.objects.filter(student=student), document_id)
        if student_document.document is None:
            raise NotFound("This document's file is no longer available")
        stream = open_document_stream(document=student_document.document)
        response = FileResponse(stream, content_type=student_document.document.content_type)
        safe_name = student_document.document.original_filename.replace('"', "")
        response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
        return response


class StudentDocumentDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, student_id, document_id):
        tenant = resolve_request_tenant(request, "students.document.manage")
        student = resolve_tenant_object(Student.objects.filter(tenant=tenant), student_id)
        student_document = resolve_tenant_object(StudentDocument.objects.filter(student=student), document_id)
        try:
            delete_student_document(user=request.user, tenant=tenant, student_document=student_document)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(status=status.HTTP_204_NO_CONTENT)

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
from apps.tenancy.models import Campus, User
from apps.tenancy.services import require_permission

from .models import Employee, EmployeeDocument, EmployeeQualification, EmploymentStatus, EmploymentType
from .services import (
    add_employee_document,
    add_employee_qualification,
    change_employment_status,
    create_employee,
    delete_employee_document,
    link_user_account,
    unlink_user_account,
    update_employee_details,
)


class StaffPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def resolve_staff_tenant(request, permission):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        membership = require_permission(user=request.user, tenant_slug=slug, permission=permission)
        require_module_enabled(tenant=membership.tenant, module_code="staff_hr")
        return membership.tenant
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error


def resolve_staff_membership(request, permission):
    """Same as resolve_staff_tenant but also returns the membership, for
    read paths that need campus_scoped() -- every write path already
    enforces campus scope via apps.staff.services._require_campus_scope,
    but until this fix the read paths (Employee list/detail, employee
    document list/download) didn't, letting a campus-scoped staff viewer
    read data for employees outside their own campus even though writing
    to the same employee was correctly denied.
    """
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        membership = require_permission(user=request.user, tenant_slug=slug, permission=permission)
        require_module_enabled(tenant=membership.tenant, module_code="staff_hr")
        return membership
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error


def campus_scoped(queryset, membership):
    """Mirrors apps.students.api.campus_scoped: a cross-campus employee
    404s as "doesn't exist" rather than being resolved and then rejected,
    same anti-enumeration shape as resolve_tenant_object's get_object_or_404.
    """
    if membership.campus_id is not None:
        return queryset.filter(campus_id=membership.campus_id)
    return queryset


def resolve_tenant_object(queryset, pk):
    try:
        return get_object_or_404(queryset, pk=pk)
    except DjangoValidationError as error:
        raise NotFound("No matching record for the given identifier") from error


def api_validation_error(error):
    return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)


class EmployeeSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Employee
        fields = [
            "id", "employee_number", "first_name", "last_name", "full_name", "date_of_birth", "national_id",
            "phone_number", "email", "campus", "department", "job_title", "employment_type", "hire_date",
            "status", "emergency_contact_name", "emergency_contact_phone", "user_account", "created_at",
        ]
        read_only_fields = ["id", "status", "user_account", "created_at"]


class EmployeeCreateSerializer(serializers.Serializer):
    employee_number = serializers.CharField(max_length=40)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    job_title = serializers.CharField(max_length=100)
    employment_type = serializers.ChoiceField(choices=EmploymentType.choices)
    hire_date = serializers.DateField()
    campus = serializers.IntegerField(required=False, allow_null=True)
    department = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    national_id = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    phone_number = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    emergency_contact_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    emergency_contact_phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")


class EmployeeUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=100, required=False)
    last_name = serializers.CharField(max_length=100, required=False)
    job_title = serializers.CharField(max_length=100, required=False)
    employment_type = serializers.ChoiceField(choices=EmploymentType.choices, required=False)
    hire_date = serializers.DateField(required=False)
    campus = serializers.IntegerField(required=False, allow_null=True)
    department = serializers.CharField(max_length=100, required=False, allow_blank=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    national_id = serializers.CharField(max_length=40, required=False, allow_blank=True)
    phone_number = serializers.CharField(max_length=30, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    emergency_contact_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    emergency_contact_phone = serializers.CharField(max_length=30, required=False, allow_blank=True)


class EmployeeListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = EmployeeSerializer
    pagination_class = StaffPagination

    def get_queryset(self):
        membership = resolve_staff_membership(self.request, "staff.view")
        queryset = campus_scoped(Employee.objects.filter(tenant=membership.tenant), membership).order_by("employee_number")
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        campus_id = self.request.query_params.get("campus")
        if campus_id:
            queryset = queryset.filter(campus_id=campus_id)
        department = self.request.query_params.get("department")
        if department:
            queryset = queryset.filter(department=department)
        return queryset

    def create(self, request, *args, **kwargs):
        tenant = resolve_staff_tenant(request, "staff.manage")
        serializer = EmployeeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        campus_id = data.pop("campus", None)
        campus = resolve_tenant_object(Campus.objects.filter(tenant=tenant), str(campus_id)) if campus_id else None
        try:
            employee = create_employee(user=request.user, tenant=tenant, campus=campus, **data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(EmployeeSerializer(employee).data, status=status.HTTP_201_CREATED)


class EmployeeDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, employee_id):
        membership = resolve_staff_membership(request, "staff.view")
        employees = campus_scoped(Employee.objects.filter(tenant=membership.tenant), membership)
        employee = resolve_tenant_object(employees, employee_id)
        return Response(EmployeeSerializer(employee).data)

    def patch(self, request, employee_id):
        tenant = resolve_staff_tenant(request, "staff.manage")
        employee = resolve_tenant_object(Employee.objects.filter(tenant=tenant), employee_id)
        serializer = EmployeeUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if "campus" in data:
            campus_id = data.pop("campus")
            data["campus"] = resolve_tenant_object(Campus.objects.filter(tenant=tenant), str(campus_id)) if campus_id else None
        try:
            updated = update_employee_details(user=request.user, tenant=tenant, employee=employee, **data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(EmployeeSerializer(updated).data)


class EmployeeStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=EmploymentStatus.choices)
    reason = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")


class EmployeeStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, employee_id):
        tenant = resolve_staff_tenant(request, "staff.manage")
        employee = resolve_tenant_object(Employee.objects.filter(tenant=tenant), employee_id)
        serializer = EmployeeStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            updated = change_employment_status(
                user=request.user, tenant=tenant, employee=employee, status=data["status"], reason=data["reason"],
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(EmployeeSerializer(updated).data)


class EmployeeDocumentSerializer(serializers.ModelSerializer):
    original_filename = serializers.CharField(source="document.original_filename", read_only=True, default=None)
    content_type = serializers.CharField(source="document.content_type", read_only=True, default=None)
    size_bytes = serializers.IntegerField(source="document.size_bytes", read_only=True, default=None)
    uploaded_at = serializers.DateTimeField(source="document.created_at", read_only=True, default=None)

    class Meta:
        model = EmployeeDocument
        fields = ["id", "document_type", "original_filename", "content_type", "size_bytes", "uploaded_at"]
        read_only_fields = fields


class EmployeeDocumentUploadSerializer(serializers.Serializer):
    document_type = serializers.CharField(max_length=80)
    file = serializers.FileField()


class EmployeeDocumentListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = EmployeeDocumentSerializer
    pagination_class = StaffPagination
    parser_classes = [MultiPartParser]

    def get_employee(self):
        membership = resolve_staff_membership(self.request, "staff.view")
        employees = campus_scoped(Employee.objects.filter(tenant=membership.tenant), membership)
        return membership.tenant, resolve_tenant_object(employees, self.kwargs["employee_id"])

    def get_queryset(self):
        _, employee = self.get_employee()
        return EmployeeDocument.objects.filter(employee=employee).order_by("-document__created_at")

    def create(self, request, *args, **kwargs):
        tenant = resolve_staff_tenant(request, "staff.manage")
        employee = resolve_tenant_object(Employee.objects.filter(tenant=tenant), self.kwargs["employee_id"])
        serializer = EmployeeDocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded_file = serializer.validated_data["file"]
        try:
            document = add_employee_document(
                user=request.user, tenant=tenant, employee=employee,
                document_type=serializer.validated_data["document_type"], file_obj=uploaded_file,
                original_filename=uploaded_file.name, content_type=uploaded_file.content_type,
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(EmployeeDocumentSerializer(document).data, status=status.HTTP_201_CREATED)


class EmployeeDocumentDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, employee_id, document_id):
        membership = resolve_staff_membership(request, "staff.view")
        employees = campus_scoped(Employee.objects.filter(tenant=membership.tenant), membership)
        employee = resolve_tenant_object(employees, employee_id)
        employee_document = resolve_tenant_object(EmployeeDocument.objects.filter(employee=employee), document_id)
        if employee_document.document is None:
            raise NotFound("This document's file is no longer available")
        stream = open_document_stream(document=employee_document.document)
        response = FileResponse(stream, content_type=employee_document.document.content_type)
        safe_name = employee_document.document.original_filename.replace('"', "")
        response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
        return response


class EmployeeDocumentDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, employee_id, document_id):
        tenant = resolve_staff_tenant(request, "staff.manage")
        employee = resolve_tenant_object(Employee.objects.filter(tenant=tenant), employee_id)
        employee_document = resolve_tenant_object(EmployeeDocument.objects.filter(employee=employee), document_id)
        try:
            delete_employee_document(user=request.user, tenant=tenant, employee_document=employee_document)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmployeeQualificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeQualification
        fields = ["id", "title", "institution", "year_obtained"]
        read_only_fields = ["id"]


class EmployeeQualificationListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = EmployeeQualificationSerializer
    pagination_class = StaffPagination

    def get_queryset(self):
        membership = resolve_staff_membership(self.request, "staff.view")
        employees = campus_scoped(Employee.objects.filter(tenant=membership.tenant), membership)
        employee = resolve_tenant_object(employees, self.kwargs["employee_id"])
        return EmployeeQualification.objects.filter(employee=employee).order_by("-year_obtained")

    def create(self, request, *args, **kwargs):
        tenant = resolve_staff_tenant(request, "staff.manage")
        employee = resolve_tenant_object(Employee.objects.filter(tenant=tenant), self.kwargs["employee_id"])
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            qualification = add_employee_qualification(user=request.user, tenant=tenant, employee=employee, **serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(EmployeeQualificationSerializer(qualification).data, status=status.HTTP_201_CREATED)


class EmployeeUserLinkSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()


class EmployeeUserLinkView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, employee_id):
        tenant = resolve_staff_tenant(request, "staff.user_link.manage")
        employee = resolve_tenant_object(Employee.objects.filter(tenant=tenant), employee_id)
        serializer = EmployeeUserLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_account = resolve_tenant_object(User.objects.filter(memberships__tenant=tenant).distinct(), str(serializer.validated_data["user_id"]))
        try:
            updated = link_user_account(user=request.user, tenant=tenant, employee=employee, user_account=user_account)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(EmployeeSerializer(updated).data)

    def delete(self, request, employee_id):
        tenant = resolve_staff_tenant(request, "staff.user_link.manage")
        employee = resolve_tenant_object(Employee.objects.filter(tenant=tenant), employee_id)
        try:
            updated = unlink_user_account(user=request.user, tenant=tenant, employee=employee)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(EmployeeSerializer(updated).data)

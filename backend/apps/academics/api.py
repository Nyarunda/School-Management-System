from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated

from apps.platform.services import require_module_enabled
from apps.tenancy.services import require_permission

from .models import AcademicLevel, AcademicYear, ClassGroup, TeacherAssignment, Term


def resolve_academics_tenant(request, permission):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        membership = require_permission(user=request.user, tenant_slug=slug, permission=permission)
        require_module_enabled(tenant=membership.tenant, module_code="academics")
        return membership.tenant
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error


def resolve_class_group_catalogue_membership(request):
    """Unlike resolve_academics_tenant above, this is gated by Attendance's
    own permission and module flag, not a generic academics one: this
    catalogue exists solely so Attendance's Open Register action has a
    legitimate class_group to submit (ACADEMIC-GAP-01), and the filtering
    in ClassGroupListView below only makes sense for attendance.session.manage
    holders -- an academics.setup.view admin who isn't a teacher has no
    TeacherAssignment rows and would otherwise see an empty, confusing list.
    """
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        membership = require_permission(user=request.user, tenant_slug=slug, permission="attendance.session.manage")
        require_module_enabled(tenant=membership.tenant, module_code="attendance")
        return membership
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error


class AcademicsPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class AcademicYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicYear
        fields = ["id", "name", "starts_on", "ends_on", "is_current"]
        read_only_fields = fields


class AcademicYearListView(ListAPIView):
    """Read-only reference catalogue -- the write side (creating an academic
    year) has no service/API anywhere in this codebase yet; this exists only
    so consumers like Finance's fee-structure-creation UI can populate a
    picker from existing rows.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = AcademicYearSerializer
    pagination_class = AcademicsPagination

    def get_queryset(self):
        tenant = resolve_academics_tenant(self.request, "academics.setup.view")
        return AcademicYear.objects.for_tenant(tenant).order_by("-starts_on")


class AcademicLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicLevel
        fields = ["id", "name", "code", "sequence"]
        read_only_fields = fields


class AcademicLevelListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AcademicLevelSerializer
    pagination_class = AcademicsPagination

    def get_queryset(self):
        tenant = resolve_academics_tenant(self.request, "academics.setup.view")
        return AcademicLevel.objects.for_tenant(tenant).order_by("sequence")


class TermSerializer(serializers.ModelSerializer):
    class Meta:
        model = Term
        fields = ["id", "academic_year", "name", "starts_on", "ends_on", "sequence"]
        read_only_fields = fields


class TermListView(ListAPIView):
    """Read-only reference catalogue -- same purpose as AcademicYearListView
    above (populating Finance's fee-structure-creation picker, now that fee
    structures are term-scoped), plus optional ?academic_year= filtering
    since a term only ever makes sense within one specific year.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = TermSerializer
    pagination_class = AcademicsPagination

    def get_queryset(self):
        tenant = resolve_academics_tenant(self.request, "academics.setup.view")
        queryset = Term.objects.for_tenant(tenant)
        academic_year_id = self.request.query_params.get("academic_year")
        if academic_year_id:
            queryset = queryset.filter(academic_year_id=academic_year_id)
        return queryset.order_by("-academic_year__starts_on", "sequence")


class ClassGroupSerializer(serializers.ModelSerializer):
    academic_level = serializers.CharField(source="academic_level.name", read_only=True)
    campus = serializers.CharField(source="campus.name", read_only=True)

    class Meta:
        model = ClassGroup
        fields = ["id", "name", "code", "stream", "academic_level", "campus"]
        read_only_fields = fields


class ClassGroupListView(ListAPIView):
    """Read-only catalogue closing ACADEMIC-GAP-01 -- the full attendance
    session lifecycle (open/mark/save/submit) already exists and is
    unaffected; this only gives the frontend a legitimate class_group to
    select. A class group is listed here only if the caller could actually
    succeed in opening a register for it: this mirrors
    apps.attendance.services._require_class_authorization's campus-then-
    any_class-then-TeacherAssignment shape exactly (campus scope checked
    unconditionally first, any_class only bypasses the TeacherAssignment
    requirement). Keep the two in sync -- do not let this list drift from
    what open_attendance_session will actually accept.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ClassGroupSerializer
    pagination_class = AcademicsPagination

    def get_queryset(self):
        membership = resolve_class_group_catalogue_membership(self.request)
        queryset = ClassGroup.objects.for_tenant(membership.tenant).select_related("academic_level", "campus")
        if membership.campus_id is not None:
            queryset = queryset.filter(campus_id=membership.campus_id)
        if "attendance.any_class" not in membership.role.permissions:
            queryset = queryset.filter(
                id__in=TeacherAssignment.objects.filter(
                    tenant=membership.tenant, teacher=self.request.user,
                ).values("class_group_id")
            )
        return queryset.order_by("name")

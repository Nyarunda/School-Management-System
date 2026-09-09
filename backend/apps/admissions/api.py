from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import AcademicYear, ClassGroup, Term
from apps.platform.services import require_module_enabled
from apps.tenancy.models import Campus
from apps.tenancy.services import require_permission

from .models import Application
from .services import enroll_application


def resolve_admissions_tenant(request, permission):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        membership = require_permission(user=request.user, tenant_slug=slug, permission=permission)
        require_module_enabled(tenant=membership.tenant, module_code="admissions")
        return membership.tenant
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error


def resolve_tenant_object(queryset, pk):
    try:
        return get_object_or_404(queryset, pk=pk)
    except DjangoValidationError as error:
        raise NotFound("No matching record for the given identifier") from error


def api_validation_error(error):
    return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)


class ApplicationEnrollSerializer(serializers.Serializer):
    admission_number = serializers.CharField(max_length=40)
    academic_year = serializers.UUIDField()
    class_group = serializers.UUIDField()
    term = serializers.UUIDField(required=False, allow_null=True, default=None)
    campus = serializers.IntegerField(required=False, allow_null=True, default=None)


class ApplicationEnrollView(APIView):
    """Closes the Admission -> Student -> academic-placement journey with
    the minimal surface needed to make it reachable: one endpoint over the
    existing enroll_application/enroll_student services. No Application
    CRUD/list/review API is added alongside this.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, application_id):
        tenant = resolve_admissions_tenant(request, "admissions.enroll")
        application = resolve_tenant_object(Application.objects.filter(tenant=tenant), application_id)
        serializer = ApplicationEnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        academic_year = resolve_tenant_object(AcademicYear.objects.filter(tenant=tenant), str(data["academic_year"]))
        class_group = resolve_tenant_object(ClassGroup.objects.filter(tenant=tenant), str(data["class_group"]))
        term = None
        if data["term"] is not None:
            term = resolve_tenant_object(Term.objects.filter(tenant=tenant), str(data["term"]))
        campus = None
        if data["campus"] is not None:
            campus = resolve_tenant_object(Campus.objects.filter(tenant=tenant), data["campus"])

        try:
            student, enrollment = enroll_application(
                user=request.user, tenant=tenant, application=application,
                admission_number=data["admission_number"], academic_year=academic_year,
                class_group=class_group, term=term, campus=campus,
            )
        except DjangoValidationError as error:
            return api_validation_error(error)

        return Response(
            {
                "student_id": str(student.id),
                "enrollment_id": str(enrollment.id),
                "application_status": "ENROLLED",
            },
            status=status.HTTP_201_CREATED,
        )

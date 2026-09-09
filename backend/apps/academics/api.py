from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated

from apps.platform.services import require_module_enabled
from apps.tenancy.services import require_permission

from .models import AcademicLevel, AcademicYear


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

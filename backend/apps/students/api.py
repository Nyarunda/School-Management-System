from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import NotFound, PermissionDenied
from apps.tenancy.models import Membership

from .selectors import list_students


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


def resolve_request_tenant(request):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    membership = Membership.objects.filter(
        tenant__slug=slug,
        tenant__is_active=True,
        user=request.user,
        is_active=True,
    ).select_related("tenant", "role").first()
    if membership is None:
        raise PermissionDenied("You do not have access to this tenant")
    if not getattr(request.user, "is_superuser", False) and "students.view" not in membership.role.permissions:
        raise PermissionDenied("User lacks permission: students.view")
    return membership.tenant


class StudentListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = StudentListPagination
    serializer_class = StudentListSerializer

    def get_queryset(self):
        return list_students(tenant=resolve_request_tenant(self.request))
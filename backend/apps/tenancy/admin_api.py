from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Campus, Membership, Role
from .permissions_catalogue import PERMISSION_CATALOGUE
from .services import (
    _UNSET,
    activate_membership,
    create_role,
    deactivate_membership,
    delete_role,
    invite_user,
    require_permission,
    update_membership,
    update_role,
)


class TenancyAdminPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def resolve_tenancy_admin_tenant(request, permission):
    """No require_module_enabled check here, deliberately -- tenant user/role
    administration is core platform functionality, not gated behind any
    business module subscription (you must always be able to manage who has
    access, regardless of which modules a tenant has bought).
    """
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        membership = require_permission(user=request.user, tenant_slug=slug, permission=permission)
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


# --- Permission catalogue ------------------------------------------------

class PermissionCatalogueView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        resolve_tenancy_admin_tenant(request, "tenancy.role.view")
        return Response([
            {"code": code, "label": label, "domain": code.split(".")[0]}
            for code, label in PERMISSION_CATALOGUE.items()
        ])


# --- Roles -----------------------------------------------------------------

class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "name", "permissions"]
        read_only_fields = ["id"]


class RoleCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    permissions = serializers.ListField(child=serializers.CharField(), default=list)


class RoleUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    permissions = serializers.ListField(child=serializers.CharField(), required=False)


class RoleListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = RoleSerializer
    pagination_class = TenancyAdminPagination

    def get_queryset(self):
        tenant = resolve_tenancy_admin_tenant(self.request, "tenancy.role.view")
        return Role.objects.filter(tenant=tenant).order_by("name")

    def post(self, request):
        tenant = resolve_tenancy_admin_tenant(request, "tenancy.role.manage")
        serializer = RoleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            role = create_role(actor=request.user, tenant=tenant, **serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(RoleSerializer(role).data, status=status.HTTP_201_CREATED)


class RoleDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, role_id):
        tenant = resolve_tenancy_admin_tenant(request, "tenancy.role.view")
        role = resolve_tenant_object(Role.objects.filter(tenant=tenant), role_id)
        return Response(RoleSerializer(role).data)

    def patch(self, request, role_id):
        tenant = resolve_tenancy_admin_tenant(request, "tenancy.role.manage")
        role = resolve_tenant_object(Role.objects.filter(tenant=tenant), role_id)
        serializer = RoleUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            role = update_role(actor=request.user, tenant=tenant, role=role, **serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(RoleSerializer(role).data)

    def delete(self, request, role_id):
        tenant = resolve_tenancy_admin_tenant(request, "tenancy.role.manage")
        role = resolve_tenant_object(Role.objects.filter(tenant=tenant), role_id)
        try:
            delete_role(actor=request.user, tenant=tenant, role=role)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Memberships -------------------------------------------------------

class MembershipSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    campus = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = ["id", "user", "role", "campus", "is_active", "joined_at"]

    def get_user(self, obj):
        return {
            "id": str(obj.user.id), "username": obj.user.get_username(), "email": obj.user.email,
            "first_name": obj.user.first_name, "last_name": obj.user.last_name,
        }

    def get_role(self, obj):
        return {"id": obj.role_id, "name": obj.role.name}

    def get_campus(self, obj):
        return {"id": obj.campus_id, "name": obj.campus.name} if obj.campus_id else None


class MembershipUpdateSerializer(serializers.Serializer):
    role = serializers.IntegerField(required=False)
    campus = serializers.IntegerField(required=False, allow_null=True)


class InviteUserSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.IntegerField()
    campus = serializers.IntegerField(required=False, allow_null=True, default=None)


class MembershipListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = MembershipSerializer
    pagination_class = TenancyAdminPagination

    def get_queryset(self):
        tenant = resolve_tenancy_admin_tenant(self.request, "tenancy.membership.view")
        return Membership.objects.filter(tenant=tenant).select_related("user", "role", "campus").order_by("user__username")


class MembershipDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, membership_id):
        tenant = resolve_tenancy_admin_tenant(request, "tenancy.membership.view")
        membership = resolve_tenant_object(
            Membership.objects.filter(tenant=tenant).select_related("user", "role", "campus"), membership_id,
        )
        return Response(MembershipSerializer(membership).data)

    def patch(self, request, membership_id):
        tenant = resolve_tenancy_admin_tenant(request, "tenancy.membership.manage")
        membership = resolve_tenant_object(Membership.objects.filter(tenant=tenant), membership_id)
        serializer = MembershipUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        role = None
        if "role" in data:
            role = resolve_tenant_object(Role.objects.filter(tenant=tenant), data["role"])
        campus = _UNSET
        if "campus" in data:
            campus_id = data["campus"]
            campus = resolve_tenant_object(Campus.objects.filter(tenant=tenant), campus_id) if campus_id is not None else None

        try:
            membership = update_membership(actor=request.user, tenant=tenant, membership=membership, role=role, campus=campus)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(MembershipSerializer(membership).data)


class MembershipActivateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, membership_id):
        tenant = resolve_tenancy_admin_tenant(request, "tenancy.membership.manage")
        membership = resolve_tenant_object(Membership.objects.filter(tenant=tenant), membership_id)
        try:
            membership = activate_membership(actor=request.user, tenant=tenant, membership=membership)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(MembershipSerializer(membership).data)


class MembershipDeactivateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, membership_id):
        tenant = resolve_tenancy_admin_tenant(request, "tenancy.membership.manage")
        membership = resolve_tenant_object(Membership.objects.filter(tenant=tenant), membership_id)
        try:
            membership = deactivate_membership(actor=request.user, tenant=tenant, membership=membership)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(MembershipSerializer(membership).data)


class UserInviteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tenant = resolve_tenancy_admin_tenant(request, "tenancy.membership.manage")
        serializer = InviteUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        role = resolve_tenant_object(Role.objects.filter(tenant=tenant), data["role"])
        campus = None
        if data.get("campus") is not None:
            campus = resolve_tenant_object(Campus.objects.filter(tenant=tenant), data["campus"])
        try:
            membership = invite_user(actor=request.user, tenant=tenant, email=data["email"], role=role, campus=campus)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(MembershipSerializer(membership).data, status=status.HTTP_201_CREATED)

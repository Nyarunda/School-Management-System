from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenancy.models import Tenant

from .catalogue import MODULE_CATALOGUE
from .models import SubscriptionPlan, TenantModuleOverride, TenantSubscription
from .services import (
    assign_plan,
    clear_module_override,
    create_plan,
    delete_plan,
    get_enabled_modules,
    set_module_override,
    update_plan,
)


class IsSuperUser(BasePermission):
    """Platform administration is cross-tenant by nature -- there is no
    Membership to check, so this bypasses require_membership/require_permission
    entirely and gates on the one platform-admin concept this codebase
    already has (see the Milestone 21 plan: reusing is_superuser rather
    than inventing a separate platform-admin flag).
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


def api_validation_error(error):
    return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = ["id", "name", "module_codes", "is_default", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class SubscriptionPlanCreateSerializer(serializers.Serializer):
    """Validates request shape only (types, a real boolean, a real list of
    strings) -- module-code membership in MODULE_CATALOGUE and the
    is_default/is_active invariants are the service layer's job
    (create_plan), since those are business rules, not input shape.
    """

    name = serializers.CharField(max_length=100)
    module_codes = serializers.ListField(child=serializers.CharField(), default=list)
    is_default = serializers.BooleanField(default=False)
    is_active = serializers.BooleanField(default=True)


class SubscriptionPlanUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    module_codes = serializers.ListField(child=serializers.CharField(), required=False)
    is_default = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)


class TenantSubscriptionAssignSerializer(serializers.Serializer):
    plan_id = serializers.UUIDField()


class TenantModuleOverrideWriteSerializer(serializers.Serializer):
    is_enabled = serializers.BooleanField()


class ModuleCatalogueView(APIView):
    permission_classes = [IsSuperUser]

    def get(self, request):
        return Response([
            {"code": code, "label": definition.label, "apps": list(definition.apps)}
            for code, definition in MODULE_CATALOGUE.items()
        ])


class SubscriptionPlanListCreateView(APIView):
    permission_classes = [IsSuperUser]

    def get(self, request):
        plans = SubscriptionPlan.objects.all().order_by("name")
        return Response(SubscriptionPlanSerializer(plans, many=True).data)

    def post(self, request):
        serializer = SubscriptionPlanCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            plan = create_plan(**serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(SubscriptionPlanSerializer(plan).data, status=status.HTTP_201_CREATED)


class SubscriptionPlanDetailView(APIView):
    permission_classes = [IsSuperUser]

    def get(self, request, plan_id):
        plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
        return Response(SubscriptionPlanSerializer(plan).data)

    def patch(self, request, plan_id):
        plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
        serializer = SubscriptionPlanUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            plan = update_plan(plan=plan, **serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(SubscriptionPlanSerializer(plan).data)

    def delete(self, request, plan_id):
        plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
        try:
            delete_plan(plan=plan)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TenantSubscriptionView(APIView):
    permission_classes = [IsSuperUser]

    def get(self, request, tenant_id):
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        subscription = TenantSubscription.objects.select_related("plan").filter(tenant=tenant).first()
        return Response({
            "tenant_id": str(tenant.id),
            "plan": SubscriptionPlanSerializer(subscription.plan).data if subscription else None,
            "enabled_modules": sorted(get_enabled_modules(tenant)),
        })

    def put(self, request, tenant_id):
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        serializer = TenantSubscriptionAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = get_object_or_404(SubscriptionPlan, pk=serializer.validated_data["plan_id"])
        try:
            assign_plan(tenant=tenant, plan=plan)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response({
            "tenant_id": str(tenant.id),
            "plan": SubscriptionPlanSerializer(plan).data,
            "enabled_modules": sorted(get_enabled_modules(tenant)),
        })


class TenantModuleOverrideListView(APIView):
    permission_classes = [IsSuperUser]

    def get(self, request, tenant_id):
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        overrides = TenantModuleOverride.objects.filter(tenant=tenant).order_by("module_code")
        return Response([
            {"module_code": override.module_code, "is_enabled": override.is_enabled}
            for override in overrides
        ])


class TenantModuleOverrideDetailView(APIView):
    """The resource identity is (tenant, module_code) -- PUT upserts the
    override for that module, matching REST semantics better than a
    POST-always-201 create endpoint would for something that's explicitly
    an upsert (set_module_override uses update_or_create).
    """

    permission_classes = [IsSuperUser]

    def put(self, request, tenant_id, module_code):
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        serializer = TenantModuleOverrideWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            override = set_module_override(
                tenant=tenant, module_code=module_code, is_enabled=serializer.validated_data["is_enabled"],
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response({"module_code": override.module_code, "is_enabled": override.is_enabled})

    def delete(self, request, tenant_id, module_code):
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        clear_module_override(tenant=tenant, module_code=module_code)
        return Response(status=status.HTTP_204_NO_CONTENT)

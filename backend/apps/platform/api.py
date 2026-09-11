from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenancy.models import AuditEvent, Tenant

from .catalogue import MODULE_CATALOGUE
from .models import PlatformAuditEvent, SubscriptionPlan, TenantModuleOverride, TenantSubscription
from .services import (
    assign_plan,
    clear_module_override,
    create_plan,
    delete_plan,
    get_enabled_modules,
    get_tenant_integrations,
    provision_tenant,
    set_module_override,
    set_tenant_channel_enabled,
    set_tenant_mpesa_active,
    set_tenant_notifications_enabled,
    set_tenant_provider_active,
    update_plan,
    update_tenant,
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


class TenantProvisionSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    slug = serializers.SlugField(max_length=80)
    admin_email = serializers.EmailField()
    admin_role_name = serializers.CharField(max_length=100, default="Administrator")
    admin_permissions = serializers.ListField(child=serializers.CharField(), required=False, allow_null=True)


class TenantListSerializer(serializers.ModelSerializer):
    """List-row shape -- computed from prefetched subscription/overrides
    rather than calling get_enabled_modules per row (which would issue two
    extra queries per tenant in a paginated list).
    """

    plan_name = serializers.SerializerMethodField()
    enabled_module_count = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = ["id", "name", "slug", "is_active", "created_at", "plan_name", "enabled_module_count"]
        read_only_fields = fields

    def get_plan_name(self, tenant):
        subscription = getattr(tenant, "subscription", None)
        return subscription.plan.name if subscription else None

    def _enabled_modules(self, tenant):
        subscription = getattr(tenant, "subscription", None)
        enabled = set(subscription.plan.module_codes) if subscription else set()
        for override in tenant.module_overrides.all():
            if override.is_enabled:
                enabled.add(override.module_code)
            else:
                enabled.discard(override.module_code)
        return enabled

    def get_enabled_module_count(self, tenant):
        return len(self._enabled_modules(tenant))


class TenantDetailSerializer(serializers.ModelSerializer):
    plan = serializers.SerializerMethodField()
    enabled_modules = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = ["id", "name", "slug", "is_active", "created_at", "plan", "enabled_modules"]
        read_only_fields = fields

    def get_plan(self, tenant):
        subscription = getattr(tenant, "subscription", None)
        return SubscriptionPlanSerializer(subscription.plan).data if subscription else None

    def get_enabled_modules(self, tenant):
        return sorted(get_enabled_modules(tenant))


class TenantUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200, required=False)
    is_active = serializers.BooleanField(required=False)


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
            plan = create_plan(actor=request.user, **serializer.validated_data)
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
            plan = update_plan(actor=request.user, plan=plan, **serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(SubscriptionPlanSerializer(plan).data)

    def delete(self, request, plan_id):
        plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
        try:
            delete_plan(actor=request.user, plan=plan)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TenantPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class TenantListCreateView(APIView):
    """GET lists every tenant on the platform (search by name/slug) -- the
    list this platform admin area never had before. POST is unchanged: the
    single Super Admin entry point for bringing a new tenant + its first
    administrator into existence (see services.provision_tenant).
    """

    permission_classes = [IsSuperUser]

    def get(self, request):
        queryset = (
            Tenant.objects.all()
            .select_related("subscription__plan")
            .prefetch_related("module_overrides")
            .order_by("-created_at")
        )
        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(slug__icontains=search))
        paginator = TenantPagination()
        page = paginator.paginate_queryset(queryset, request)
        return paginator.get_paginated_response(TenantListSerializer(page, many=True).data)

    def post(self, request):
        serializer = TenantProvisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            tenant = provision_tenant(
                actor=request.user, name=data["name"], slug=data["slug"], admin_email=data["admin_email"],
                admin_role_name=data["admin_role_name"], admin_permissions=data.get("admin_permissions"),
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(
            {"tenant_id": str(tenant.id), "slug": tenant.slug, "name": tenant.name},
            status=status.HTTP_201_CREATED,
        )


class TenantDetailView(APIView):
    permission_classes = [IsSuperUser]

    def get(self, request, tenant_id):
        tenant = get_object_or_404(Tenant.objects.select_related("subscription__plan"), pk=tenant_id)
        return Response(TenantDetailSerializer(tenant).data)

    def patch(self, request, tenant_id):
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        serializer = TenantUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        tenant = update_tenant(actor=request.user, tenant=tenant, **serializer.validated_data)
        return Response(TenantDetailSerializer(tenant).data)


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
            assign_plan(actor=request.user, tenant=tenant, plan=plan)
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
                actor=request.user, tenant=tenant, module_code=module_code,
                is_enabled=serializer.validated_data["is_enabled"],
            )
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response({"module_code": override.module_code, "is_enabled": override.is_enabled})

    def delete(self, request, tenant_id, module_code):
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        clear_module_override(actor=request.user, tenant=tenant, module_code=module_code)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TenantIntegrationsPatchSerializer(serializers.Serializer):
    notifications_enabled = serializers.BooleanField()


class TenantIntegrationChannelToggleSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    provider_active = serializers.BooleanField(required=False)

    def validate(self, data):
        if "enabled" not in data and "provider_active" not in data:
            raise serializers.ValidationError("Provide 'enabled' and/or 'provider_active'")
        return data


class TenantIntegrationMpesaToggleSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()


class TenantIntegrationsView(APIView):
    """Status-only view of a tenant's notification/M-Pesa integrations for
    the platform admin -- see services.get_tenant_integrations for why
    credentials never appear here. PATCH only toggles the tenant-wide
    notifications kill switch; per-channel/provider/mpesa toggles are
    separate resources below.
    """

    permission_classes = [IsSuperUser]

    def get(self, request, tenant_id):
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        return Response(get_tenant_integrations(tenant))

    def patch(self, request, tenant_id):
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        serializer = TenantIntegrationsPatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        set_tenant_notifications_enabled(
            actor=request.user, tenant=tenant, enabled=serializer.validated_data["notifications_enabled"],
        )
        return Response(get_tenant_integrations(tenant))


class TenantIntegrationChannelView(APIView):
    permission_classes = [IsSuperUser]

    def put(self, request, tenant_id, channel):
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        serializer = TenantIntegrationChannelToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            if "enabled" in data:
                set_tenant_channel_enabled(actor=request.user, tenant=tenant, channel=channel, enabled=data["enabled"])
            if "provider_active" in data:
                set_tenant_provider_active(actor=request.user, tenant=tenant, channel=channel, is_active=data["provider_active"])
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(get_tenant_integrations(tenant))


class TenantIntegrationMpesaView(APIView):
    permission_classes = [IsSuperUser]

    def put(self, request, tenant_id):
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        serializer = TenantIntegrationMpesaToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            set_tenant_mpesa_active(actor=request.user, tenant=tenant, is_active=serializer.validated_data["is_active"])
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(get_tenant_integrations(tenant))


class PlatformAuditPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class PlatformAuditEventSerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField()

    class Meta:
        model = PlatformAuditEvent
        fields = ["id", "actor", "action", "resource_type", "resource_id", "metadata", "created_at"]

    def get_actor(self, obj):
        return obj.actor.username if obj.actor else None


class TenantAuditEventSerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField()

    class Meta:
        model = AuditEvent
        fields = ["id", "actor", "action", "resource_type", "resource_id", "metadata", "created_at"]

    def get_actor(self, obj):
        return obj.actor.username if obj.actor else None


class PlatformAuditEventListView(ListAPIView):
    permission_classes = [IsSuperUser]
    serializer_class = PlatformAuditEventSerializer
    pagination_class = PlatformAuditPagination

    def get_queryset(self):
        queryset = PlatformAuditEvent.objects.all().order_by("-created_at")
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(action__icontains=search) | Q(resource_type__icontains=search) | Q(resource_id__icontains=search)
            )
        return queryset


class TenantAuditEventListView(ListAPIView):
    permission_classes = [IsSuperUser]
    serializer_class = TenantAuditEventSerializer
    pagination_class = PlatformAuditPagination

    def get_queryset(self):
        tenant = get_object_or_404(Tenant, pk=self.kwargs["tenant_id"])
        return AuditEvent.objects.for_tenant(tenant).order_by("-created_at")

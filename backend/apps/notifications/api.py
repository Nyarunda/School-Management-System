from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenancy.services import require_membership, require_permission

from .models import (
    CommunicationChannel,
    CommunicationSetup,
    GuardianRecipientPolicy,
    NotificationChannel,
    NotificationDeliveryAttempt,
    NotificationOutbox,
    NotificationProviderConfig,
    NotificationRecipientType,
    NotificationRule,
    NotificationTemplate,
    UserNotification,
)
from .services import (
    configure_communication_setup,
    configure_provider,
    create_notification_rule,
    create_notification_template,
    mark_user_notification_read,
    retry_notification,
    set_channel_enabled,
    update_notification_rule,
    update_notification_template,
)


class NotificationPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def resolve_notifications_tenant(request, permission):
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        return require_permission(user=request.user, tenant_slug=slug, permission=permission).tenant
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error


def resolve_member_tenant(request):
    """For personal-inbox endpoints -- any active member of the tenant, no
    business permission required (this is the user's own inbox, not an
    admin view of the tenant's notification record).
    """
    slug = request.headers.get("X-Tenant-Slug")
    if not slug:
        raise NotFound("Tenant context is required")
    try:
        return require_membership(user=request.user, tenant_slug=slug).tenant
    except DjangoValidationError as error:
        raise PermissionDenied(error.messages) from error


def resolve_tenant_object(queryset, pk):
    try:
        return get_object_or_404(queryset, pk=pk)
    except DjangoValidationError as error:
        raise NotFound("No matching record for the given identifier") from error


def api_validation_error(error):
    return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)


# --- Setup -----------------------------------------------------------------

class CommunicationSetupSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommunicationSetup
        fields = ["notifications_enabled", "default_country_code"]


class CommunicationSetupView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = resolve_notifications_tenant(request, "notifications.setup.view")
        setup = CommunicationSetup.objects.filter(tenant=tenant).first()
        if setup is None:
            return Response({"notifications_enabled": True, "default_country_code": ""})
        return Response(CommunicationSetupSerializer(setup).data)

    def patch(self, request):
        tenant = resolve_notifications_tenant(request, "notifications.setup.manage")
        serializer = CommunicationSetupSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            setup = configure_communication_setup(user=request.user, tenant=tenant, **serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(CommunicationSetupSerializer(setup).data)


class CommunicationChannelSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommunicationChannel
        fields = ["channel", "enabled"]


class CommunicationChannelDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, channel):
        tenant = resolve_notifications_tenant(request, "notifications.setup.view")
        row = CommunicationChannel.objects.filter(tenant=tenant, channel=channel).first()
        if row is None:
            return Response({"channel": channel, "enabled": False})
        return Response(CommunicationChannelSerializer(row).data)

    def patch(self, request, channel):
        tenant = resolve_notifications_tenant(request, "notifications.setup.manage")
        enabled = request.data.get("enabled")
        if not isinstance(enabled, bool):
            return Response({"detail": ["'enabled' must be a boolean"]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            row = set_channel_enabled(user=request.user, tenant=tenant, channel=channel, enabled=enabled)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(CommunicationChannelSerializer(row).data)


class NotificationProviderConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationProviderConfig
        fields = ["channel", "provider", "sender_id", "is_active", "updated_at"]  # secrets are deliberately never serialized


class ProviderConfigureSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=NotificationChannel.choices)
    provider = serializers.CharField(max_length=40)
    sender_id = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    api_key = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    api_secret = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    is_active = serializers.BooleanField(required=False, default=True)


class NotificationProviderListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = resolve_notifications_tenant(request, "notifications.setup.view")
        configs = NotificationProviderConfig.objects.filter(tenant=tenant).order_by("channel")
        return Response(NotificationProviderConfigSerializer(configs, many=True).data)

    def post(self, request):
        tenant = resolve_notifications_tenant(request, "notifications.setup.manage")
        serializer = ProviderConfigureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            config = configure_provider(user=request.user, tenant=tenant, **serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(NotificationProviderConfigSerializer(config).data, status=status.HTTP_201_CREATED)


# --- Templates ---------------------------------------------------------

class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationTemplate
        fields = ["id", "code", "name", "channel", "subject", "body", "is_active"]
        read_only_fields = ["id"]


class NotificationTemplateCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=80)
    name = serializers.CharField(max_length=120)
    channel = serializers.ChoiceField(choices=NotificationChannel.choices)
    subject = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    body = serializers.CharField()
    is_active = serializers.BooleanField(required=False, default=True)


class NotificationTemplateUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False)
    subject = serializers.CharField(max_length=200, required=False, allow_blank=True)
    body = serializers.CharField(required=False)
    is_active = serializers.BooleanField(required=False)


class NotificationTemplateListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationTemplateSerializer
    pagination_class = NotificationPagination

    def get_queryset(self):
        return NotificationTemplate.objects.filter(tenant=resolve_notifications_tenant(self.request, "notifications.templates.view")).order_by("code")

    def create(self, request, *args, **kwargs):
        tenant = resolve_notifications_tenant(request, "notifications.templates.manage")
        serializer = NotificationTemplateCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            template = create_notification_template(user=request.user, tenant=tenant, **serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(NotificationTemplateSerializer(template).data, status=status.HTTP_201_CREATED)


class NotificationTemplateDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, template_id):
        tenant = resolve_notifications_tenant(request, "notifications.templates.manage")
        template = resolve_tenant_object(NotificationTemplate.objects.filter(tenant=tenant), template_id)
        serializer = NotificationTemplateUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            updated = update_notification_template(user=request.user, tenant=tenant, template=template, **serializer.validated_data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(NotificationTemplateSerializer(updated).data)


# --- Rules -----------------------------------------------------------------

class NotificationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationRule
        fields = ["id", "event_code", "recipient_type", "recipient_policy", "channel", "template", "enabled"]
        read_only_fields = ["id"]


class NotificationRuleCreateSerializer(serializers.Serializer):
    event_code = serializers.CharField(max_length=80)
    recipient_type = serializers.ChoiceField(choices=NotificationRecipientType.choices)
    recipient_policy = serializers.ChoiceField(choices=GuardianRecipientPolicy.choices, required=False, allow_null=True, default=None)
    channel = serializers.ChoiceField(choices=NotificationChannel.choices)
    template = serializers.UUIDField()
    enabled = serializers.BooleanField(required=False, default=True)


class NotificationRuleUpdateSerializer(serializers.Serializer):
    template = serializers.UUIDField(required=False)
    enabled = serializers.BooleanField(required=False)
    recipient_policy = serializers.ChoiceField(choices=GuardianRecipientPolicy.choices, required=False)


class NotificationRuleListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationRuleSerializer
    pagination_class = NotificationPagination

    def get_queryset(self):
        return NotificationRule.objects.filter(tenant=resolve_notifications_tenant(self.request, "notifications.rules.view")).order_by("event_code")

    def create(self, request, *args, **kwargs):
        tenant = resolve_notifications_tenant(request, "notifications.rules.manage")
        serializer = NotificationRuleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        template = resolve_tenant_object(NotificationTemplate.objects.filter(tenant=tenant), str(data.pop("template")))
        try:
            rule = create_notification_rule(user=request.user, tenant=tenant, template=template, **data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(NotificationRuleSerializer(rule).data, status=status.HTTP_201_CREATED)


class NotificationRuleDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, rule_id):
        tenant = resolve_notifications_tenant(request, "notifications.rules.manage")
        rule = resolve_tenant_object(NotificationRule.objects.filter(tenant=tenant), rule_id)
        serializer = NotificationRuleUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if "template" in data:
            data["template"] = resolve_tenant_object(NotificationTemplate.objects.filter(tenant=tenant), str(data["template"]))
        try:
            updated = update_notification_rule(user=request.user, tenant=tenant, rule=rule, **data)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(NotificationRuleSerializer(updated).data)


# --- Outbox ------------------------------------------------------------

class NotificationOutboxSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationOutbox
        fields = [
            "id", "channel", "recipient", "recipient_user", "message_type", "status", "attempts", "last_error",
            "created_at", "processed_at",
        ]
        read_only_fields = fields


class NotificationOutboxListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationOutboxSerializer
    pagination_class = NotificationPagination

    def get_queryset(self):
        tenant = resolve_notifications_tenant(self.request, "notifications.record.view")
        queryset = NotificationOutbox.objects.filter(tenant=tenant).order_by("-created_at")
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        channel_param = self.request.query_params.get("channel")
        if channel_param:
            queryset = queryset.filter(channel=channel_param)
        return queryset


class NotificationOutboxDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, outbox_id):
        tenant = resolve_notifications_tenant(request, "notifications.record.view")
        outbox = resolve_tenant_object(NotificationOutbox.objects.filter(tenant=tenant), outbox_id)
        return Response(NotificationOutboxSerializer(outbox).data)


class NotificationOutboxRetryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, outbox_id):
        tenant = resolve_notifications_tenant(request, "notifications.retry")
        outbox = resolve_tenant_object(NotificationOutbox.objects.filter(tenant=tenant), outbox_id)
        try:
            updated = retry_notification(user=request.user, tenant=tenant, outbox=outbox)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(NotificationOutboxSerializer(updated).data)


class NotificationDeliveryAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationDeliveryAttempt
        fields = ["id", "attempt_number", "provider", "started_at", "completed_at", "status", "provider_reference", "error_code", "error_message"]
        read_only_fields = fields


class NotificationOutboxDeliveriesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, outbox_id):
        tenant = resolve_notifications_tenant(request, "notifications.record.view")
        outbox = resolve_tenant_object(NotificationOutbox.objects.filter(tenant=tenant), outbox_id)
        attempts = outbox.delivery_attempts.order_by("attempt_number")
        return Response(NotificationDeliveryAttemptSerializer(attempts, many=True).data)


# --- In-app inbox ------------------------------------------------------

class UserNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserNotification
        fields = ["id", "title", "message", "resource_type", "resource_id", "read_at", "created_at"]
        read_only_fields = fields


class UserNotificationInboxView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserNotificationSerializer
    pagination_class = NotificationPagination

    def get_queryset(self):
        tenant = resolve_member_tenant(self.request)
        return UserNotification.objects.filter(tenant=tenant, user=self.request.user).order_by("-created_at")


class UserNotificationMarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, notification_id):
        tenant = resolve_member_tenant(request)
        notification = resolve_tenant_object(UserNotification.objects.filter(tenant=tenant), notification_id)
        try:
            updated = mark_user_notification_read(user=request.user, notification=notification)
        except DjangoValidationError as error:
            return api_validation_error(error)
        return Response(UserNotificationSerializer(updated).data)

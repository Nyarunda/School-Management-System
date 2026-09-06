from decimal import Decimal

from rest_framework import serializers, status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.students.models import Student

from .api import FinancePagination, resolve_finance_tenant, resolve_tenant_object
from .models import Invoice, MpesaCallbackLog, MpesaCallbackStatus, MpesaStkPushRequest, MpesaStkPushStatus, MpesaCallbackType, TenantMpesaConfiguration
from .mpesa_services import (
    configure_mpesa_gateway,
    handle_c2b_validation,
    initiate_stk_push,
    log_mpesa_callback,
    resolve_mpesa_tenant,
    rotate_mpesa_callback_token,
    verify_mpesa_callback, process_mpesa_callback, reject_mpesa_callback, query_stk_request, identify_stk_request,
)


class MpesaConfigurationSerializer(serializers.Serializer):
    environment = serializers.ChoiceField(choices=["SANDBOX", "PRODUCTION"])
    shortcode = serializers.CharField(max_length=20)
    consumer_key = serializers.CharField(max_length=500)
    consumer_secret = serializers.CharField(max_length=500)
    passkey = serializers.CharField(max_length=500)


def _config_response_data(config):
    # Deliberately excludes consumer_key/consumer_secret/passkey -- never
    # serialize these back out, on this or any future read endpoint.
    return {
        "id": config.id,
        "environment": config.environment,
        "shortcode": config.shortcode,
        "is_active": config.is_active,
        "callback_token": config.callback_token,
    }


class MpesaConfigurationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = resolve_finance_tenant(request, "finance.mpesa.configure")
        config = TenantMpesaConfiguration.objects.filter(tenant=tenant).first()
        if config is None:
            raise NotFound("M-Pesa is not configured")
        return Response(_config_response_data(config))

    def post(self, request):
        tenant = resolve_finance_tenant(request, "finance.mpesa.configure")
        serializer = MpesaConfigurationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        config = configure_mpesa_gateway(
            user=request.user, tenant=tenant, environment=data["environment"], shortcode=data["shortcode"],
            consumer_key=data["consumer_key"], consumer_secret=data["consumer_secret"], passkey=data["passkey"],
        )
        return Response(_config_response_data(config), status=status.HTTP_201_CREATED)


class MpesaCallbackTokenRotateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tenant = resolve_finance_tenant(request, "finance.mpesa.configure")
        config = rotate_mpesa_callback_token(user=request.user, tenant=tenant)
        return Response(_config_response_data(config))


class StkPushInitiateSerializer(serializers.Serializer):
    idempotency_key = serializers.CharField(max_length=120)
    student = serializers.UUIDField()
    phone_number = serializers.CharField(max_length=20)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("1"))
    invoice = serializers.UUIDField(required=False, allow_null=True)

    def validate_amount(self, amount):
        if amount != amount.to_integral_value():
            raise serializers.ValidationError("STK amounts must be whole shillings")
        return amount


class StkPushInitiateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tenant = resolve_finance_tenant(request, "finance.mpesa.stk_push.initiate")
        serializer = StkPushInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        student = resolve_tenant_object(Student.objects.for_tenant(tenant), str(data["student"]))
        invoice = None
        if data.get("invoice"):
            invoice = resolve_tenant_object(Invoice.objects.for_tenant(tenant), str(data["invoice"]))
        stk_request = initiate_stk_push(
            user=request.user, tenant=tenant, student=student, phone_number=data["phone_number"],
            amount=data["amount"], invoice=invoice, idempotency_key=data["idempotency_key"],
        )
        return Response(
            {
                "id": stk_request.id,
                "status": stk_request.status,
                "checkout_request_id": stk_request.checkout_request_id,
                "phone_number": stk_request.phone_number,
                "amount": stk_request.amount,
            },
            status=status.HTTP_202_ACCEPTED if stk_request.status in (MpesaStkPushStatus.INITIATING, MpesaStkPushStatus.UNKNOWN) else status.HTTP_201_CREATED,
        )


def _resolve_mpesa_config_or_404(callback_token):
    try:
        return resolve_mpesa_tenant(callback_token=callback_token)
    except TenantMpesaConfiguration.DoesNotExist as error:
        raise NotFound("No matching M-Pesa configuration for this callback URL") from error


class MpesaC2BValidationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, callback_token):
        config = _resolve_mpesa_config_or_404(callback_token)
        log_mpesa_callback(tenant=config.tenant, callback_type=MpesaCallbackType.C2B_VALIDATION, payload=request.data)
        return Response(handle_c2b_validation(tenant=config.tenant, payload=request.data))


class MpesaC2BConfirmationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, callback_token):
        config = _resolve_mpesa_config_or_404(callback_token)
        log_mpesa_callback(tenant=config.tenant, callback_type=MpesaCallbackType.C2B_CONFIRMATION, payload=request.data)
        # Acknowledge durable delivery, not financial settlement. The token
        # routes the callback; only independent verification permits posting.
        return Response({"ResultCode": 0, "ResultDesc": "Received"})


class MpesaStkCallbackView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, callback_token, request_id=None):
        config = _resolve_mpesa_config_or_404(callback_token)
        log_mpesa_callback(tenant=config.tenant, callback_type=MpesaCallbackType.STK_CALLBACK,
                          payload=request.data, request_id=request_id)
        return Response({"ResultCode": 0, "ResultDesc": "Received"})


class StkRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = MpesaStkPushRequest
        fields = ["id", "student", "invoice", "phone_number", "amount", "idempotency_key", "status",
                  "checkout_request_id", "merchant_request_id", "confirmed_receipt", "incoming_payment",
                  "result_code", "result_description", "provider_query", "queried_at", "created_at"]
        read_only_fields = fields


class StkRequestListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = StkRequestSerializer
    pagination_class = FinancePagination

    def get_queryset(self):
        tenant = resolve_finance_tenant(self.request, "finance.mpesa.stk_push.view")
        return MpesaStkPushRequest.objects.for_tenant(tenant).order_by("-created_at", "-id")


class StkRequestDetailView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = StkRequestSerializer
    lookup_url_kwarg = "request_id"

    def get_queryset(self):
        tenant = resolve_finance_tenant(self.request, "finance.mpesa.stk_push.view")
        return MpesaStkPushRequest.objects.for_tenant(tenant)


class StkRequestQueryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        tenant = resolve_finance_tenant(request, "finance.mpesa.stk_push.query")
        item = resolve_tenant_object(MpesaStkPushRequest.objects.for_tenant(tenant), request_id)
        result = query_stk_request(user=request.user, tenant=tenant, request=item)
        return Response(StkRequestSerializer(result).data)


class StkIdentifySerializer(serializers.Serializer):
    checkout_request_id = serializers.CharField(max_length=60)
    merchant_request_id = serializers.CharField(max_length=60)
    evidence = serializers.CharField(max_length=240)


class StkRequestIdentifyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        tenant = resolve_finance_tenant(request, "finance.mpesa.stk_push.reconcile")
        serializer = StkIdentifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = identify_stk_request(user=request.user, tenant=tenant, request_id=request_id, **serializer.validated_data)
        return Response(StkRequestSerializer(result).data)


class CallbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = MpesaCallbackLog
        fields = ["id", "callback_type", "provider_transaction_id", "request_id", "status", "created_at",
                  "verified_by", "verified_at", "verification_reference", "processed_at", "attempts", "last_error"]
        read_only_fields = fields


class CallbackDetailSerializer(CallbackSerializer):
    class Meta(CallbackSerializer.Meta):
        fields = CallbackSerializer.Meta.fields + ["raw_payload"]
        read_only_fields = fields


class CallbackListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CallbackSerializer
    pagination_class = FinancePagination

    def get_queryset(self):
        tenant = resolve_finance_tenant(self.request, "finance.mpesa.callback.view")
        queryset = MpesaCallbackLog.objects.filter(tenant=tenant).order_by("-created_at", "-id")
        value = self.request.query_params.get("status")
        if value:
            value = serializers.ChoiceField(choices=MpesaCallbackStatus.choices).run_validation(value)
            queryset = queryset.filter(status=value)
        return queryset


class CallbackDetailView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CallbackDetailSerializer
    lookup_url_kwarg = "callback_id"

    def get_queryset(self):
        tenant = resolve_finance_tenant(self.request, "finance.mpesa.callback.view")
        return MpesaCallbackLog.objects.filter(tenant=tenant)


class CallbackVerifySerializer(serializers.Serializer):
    evidence = serializers.CharField(max_length=240)


class CallbackVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, callback_id):
        tenant = resolve_finance_tenant(request, "finance.mpesa.callback.verify")
        serializer = CallbackVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = verify_mpesa_callback(user=request.user, tenant=tenant, callback_id=callback_id,
                                     evidence=serializer.validated_data["evidence"])
        return Response(CallbackSerializer(item).data)


class CallbackProcessView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, callback_id):
        tenant = resolve_finance_tenant(request, "finance.mpesa.callback.process")
        item = process_mpesa_callback(user=request.user, tenant=tenant, callback_id=callback_id)
        return Response(CallbackSerializer(item).data, status=400 if item.status == MpesaCallbackStatus.FAILED else 200)


class CallbackRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=240)


class CallbackRejectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, callback_id):
        tenant = resolve_finance_tenant(request, "finance.mpesa.callback.verify")
        serializer = CallbackRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = reject_mpesa_callback(user=request.user, tenant=tenant, callback_id=callback_id,
                                     reason=serializer.validated_data["reason"])
        return Response(CallbackSerializer(item).data)

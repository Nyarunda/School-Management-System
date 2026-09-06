from decimal import Decimal

from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.students.models import Student

from .api import resolve_finance_tenant, resolve_tenant_object
from .models import Invoice, MpesaCallbackType, TenantMpesaConfiguration
from .mpesa_services import (
    configure_mpesa_gateway,
    handle_c2b_confirmation,
    handle_c2b_validation,
    handle_stk_callback,
    initiate_stk_push,
    log_mpesa_callback,
    resolve_mpesa_tenant,
    rotate_mpesa_callback_token,
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
    student = serializers.UUIDField()
    phone_number = serializers.CharField(max_length=20)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    invoice = serializers.UUIDField(required=False, allow_null=True)


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
            amount=data["amount"], invoice=invoice,
        )
        return Response(
            {
                "id": stk_request.id,
                "status": stk_request.status,
                "checkout_request_id": stk_request.checkout_request_id,
                "phone_number": stk_request.phone_number,
                "amount": stk_request.amount,
            },
            status=status.HTTP_201_CREATED,
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
        log_mpesa_callback(
            tenant=config.tenant, callback_type=MpesaCallbackType.C2B_VALIDATION, payload=request.data,
            provider_transaction_id=str(request.data.get("TransID", "")),
        )
        return Response(handle_c2b_validation(tenant=config.tenant, payload=request.data))


class MpesaC2BConfirmationView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, callback_token):
        config = _resolve_mpesa_config_or_404(callback_token)
        # Log first, unconditionally, before attempting to process -- see
        # mpesa_services.log_mpesa_callback's docstring. No exception
        # handling around the call below: if it raises, that propagates to
        # DRF's default/global handler (400 for a recognized ValidationError,
        # 500 otherwise) so Safaricom retries instead of being told a
        # transaction that was never recorded is "done".
        log_mpesa_callback(
            tenant=config.tenant, callback_type=MpesaCallbackType.C2B_CONFIRMATION, payload=request.data,
            provider_transaction_id=str(request.data.get("TransID", "")),
        )
        return Response(handle_c2b_confirmation(tenant=config.tenant, config=config, payload=request.data))


class MpesaStkCallbackView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, callback_token):
        config = _resolve_mpesa_config_or_404(callback_token)
        checkout_request_id = (
            request.data.get("Body", {}).get("stkCallback", {}).get("CheckoutRequestID", "")
        )
        log_mpesa_callback(
            tenant=config.tenant, callback_type=MpesaCallbackType.STK_CALLBACK, payload=request.data,
            provider_transaction_id=str(checkout_request_id),
        )
        handle_stk_callback(tenant=config.tenant, config=config, payload=request.data)
        return Response({"ResultCode": 0, "ResultDesc": "Success"})

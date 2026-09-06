import re
import secrets
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.activity.services import record_activity
from apps.tenancy.models import Membership, Role, Tenant, User
from apps.tenancy.services import require_permission

from .models import (
    MpesaCallbackLog,
    MpesaCallbackStatus,
    MpesaCallbackType,
    MpesaStkPushRequest,
    MpesaStkPushStatus,
    PaymentMethod,
    ReconciliationStatus,
    TenantMpesaConfiguration,
    validate_same_tenant,
)
from .mpesa_client import MpesaApiError, MpesaClient, whole_shilling_amount
from .services import ingest_incoming_payment, match_incoming_payment

SYSTEM_ROLE_PERMISSIONS = [
    "finance.reconciliation.ingest",
    "finance.reconciliation.match",
    "finance.payment.record",
    "finance.payment.allocate",
]


def _normalize_msisdn(raw):
    digits = raw.strip().lstrip("+")
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    if not re.fullmatch(r"254[17]\d{8}", digits):
        raise ValidationError(f"'{raw}' is not a recognizable Kenyan phone number")
    return digits


@transaction.atomic
def configure_mpesa_gateway(*, user, tenant, environment, shortcode, consumer_key, consumer_secret, passkey):
    """Idempotent: safe to call again to rotate credentials -- rotating
    credentials never touches callback_token (see rotate_mpesa_callback_token
    for the separate, explicit "the token itself was compromised" action).
    Creates the tenant's M-Pesa PaymentMethod and system user/role/
    membership on first call only.
    """
    require_permission(user=user, tenant=tenant, permission="finance.mpesa.configure")
    if environment == "PRODUCTION" and not settings.PRODUCTION:
        raise ValidationError("Production gateway credentials require production deployment settings")
    # Configuration is rare. Serialize first-time setup on its school's row,
    # before creating any related records; rollback leaves no partial setup.
    Tenant.objects.select_for_update().get(pk=tenant.pk, is_active=True)
    if any(not isinstance(value, str) or not value or len(value) > 500
           for value in (consumer_key, consumer_secret, passkey)):
        raise ValidationError("Gateway credentials must contain between 1 and 500 characters")
    payment_method, _ = PaymentMethod.objects.get_or_create(tenant=tenant, code="MPESA", defaults={"name": "M-Pesa"})
    config = TenantMpesaConfiguration.objects.filter(tenant=tenant).first()
    if config is None:
        system_user = User.objects.create_user(username=f"mpesa-gateway-{tenant.id}", password=None)
        system_user.set_unusable_password()
        system_user.save(update_fields=["password"])
        role = Role.objects.create(tenant=tenant, name="M-Pesa Gateway", permissions=list(SYSTEM_ROLE_PERMISSIONS))
        Membership.objects.create(tenant=tenant, user=system_user, role=role)
        config = TenantMpesaConfiguration(
            tenant=tenant, payment_method=payment_method, system_user=system_user,
            callback_token=secrets.token_urlsafe(32),
        )
    config.environment = environment
    config.shortcode = shortcode
    config.consumer_key = consumer_key
    config.consumer_secret = consumer_secret
    config.passkey = passkey
    config.save()
    return config


@transaction.atomic
def rotate_mpesa_callback_token(*, user, tenant):
    """Explicit, separate action for "the callback URL was compromised" --
    never done implicitly by configure_mpesa_gateway. Safaricom's registered
    URLs must be updated to match after calling this.
    """
    require_permission(user=user, tenant=tenant, permission="finance.mpesa.configure")
    Tenant.objects.select_for_update().get(pk=tenant.pk, is_active=True)
    try:
        config = TenantMpesaConfiguration.objects.get(tenant=tenant)
    except TenantMpesaConfiguration.DoesNotExist as error:
        raise ValidationError("M-Pesa is not configured for this school") from error
    config.callback_token = secrets.token_urlsafe(32)
    config.save(update_fields=["callback_token"])
    return config


def resolve_mpesa_tenant(*, callback_token):
    """The callback_token identifies which tenant a webhook belongs to and
    is a possession secret (Safaricom and whoever configured the callback
    URL know it) -- but it is not, on its own, a strong authentication
    boundary: URLs containing it can end up in access logs, proxy logs, or
    support screenshots. It gates routing, not full trust; the payload
    itself is still validated field-by-field before any money is recorded.
    """
    config = (
        TenantMpesaConfiguration.objects.filter(callback_token=callback_token, is_active=True)
        .select_related("tenant", "payment_method", "system_user")
        .first()
    )
    if config is None:
        raise TenantMpesaConfiguration.DoesNotExist
    return config


def initiate_stk_push(*, user, tenant, student, phone_number, amount, invoice=None,
                      transaction_desc="School fees", idempotency_key=None):
    require_permission(user=user, tenant=tenant, permission="finance.mpesa.stk_push.initiate")
    try:
        amount = whole_shilling_amount(amount)
    except MpesaApiError as error:
        raise ValidationError(str(error)) from error
    validate_same_tenant(tenant=tenant, student=student, **({"invoice": invoice} if invoice else {}))
    if invoice is not None and invoice.student_id != student.pk:
        raise ValidationError("Invoice must belong to the selected student")
    normalized_phone = _normalize_msisdn(phone_number)
    try:
        config = TenantMpesaConfiguration.objects.get(tenant=tenant, is_active=True)
    except TenantMpesaConfiguration.DoesNotExist as error:
        raise ValidationError("M-Pesa is not configured for this school") from error
    key = idempotency_key or secrets.token_urlsafe(32)
    if not isinstance(key, str) or not key.strip() or len(key) > 120:
        raise ValidationError("An idempotency key of at most 120 characters is required")
    # Commit local intent before external I/O. A repeated key NEVER resends an
    # uncertain collection request (including process death while INITIATING).
    with transaction.atomic(durable=True):
        request, created = MpesaStkPushRequest.objects.get_or_create(
            tenant=tenant, idempotency_key=key,
            defaults=dict(student=student, invoice=invoice, phone_number=normalized_phone,
                          amount=amount, account_reference=student.admission_number,
                          status=MpesaStkPushStatus.INITIATING),
        )
        if not created:
            if (request.student_id, request.invoice_id, request.phone_number, request.amount) != (
                student.pk, invoice.pk if invoice else None, normalized_phone, amount
            ):
                raise ValidationError("Idempotency key already used with different STK details")
            return request
    callback_url = f"{settings.PUBLIC_BASE_URL}/api/v1/finance/mpesa/{config.callback_token}/stk/callback/{request.id}/"
    try:
        response = MpesaClient(config).stk_push(
            phone_number=normalized_phone, amount=amount, account_reference=student.admission_number,
            transaction_desc=transaction_desc, callback_url=callback_url,
        )
        if any(not isinstance(response.get(key), str) or not 0 < len(response[key]) <= 60
               for key in ("MerchantRequestID", "CheckoutRequestID")):
            raise MpesaApiError("Invalid STK response")
    except MpesaApiError:
        MpesaStkPushRequest.objects.filter(pk=request.pk, status=MpesaStkPushStatus.INITIATING).update(
            status=MpesaStkPushStatus.UNKNOWN, result_description="Provider outcome unknown; reconcile before retrying",
        )
        request.refresh_from_db()
        return request
    with transaction.atomic():
        locked = MpesaStkPushRequest.objects.select_for_update().get(tenant=tenant, pk=request.pk)
        if locked.checkout_request_id and locked.checkout_request_id != response["CheckoutRequestID"]:
            raise ValidationError("STK response conflicts with the recorded checkout")
        if locked.status in (MpesaStkPushStatus.INITIATING, MpesaStkPushStatus.UNKNOWN):
            locked.merchant_request_id = response["MerchantRequestID"]
            locked.checkout_request_id = response["CheckoutRequestID"]
            locked.status = MpesaStkPushStatus.PENDING
            locked.save(update_fields=["merchant_request_id", "checkout_request_id", "status"])
        return locked


def log_mpesa_callback(*, tenant, callback_type, payload, provider_transaction_id="", request_id=None):
    """Durable inbox. Public delivery is evidence to review, not proof of payment."""
    if not provider_transaction_id and isinstance(payload, dict):
        if callback_type == MpesaCallbackType.STK_CALLBACK:
            body = payload.get("Body")
            callback = body.get("stkCallback") if isinstance(body, dict) else None
            provider_transaction_id = callback.get("CheckoutRequestID", "") if isinstance(callback, dict) else ""
        else:
            provider_transaction_id = payload.get("TransID", "")
    return MpesaCallbackLog.objects.create(
        tenant=tenant, callback_type=callback_type, raw_payload=payload,
        provider_transaction_id=str(provider_transaction_id)[:100], request_id=request_id,
    )


def _money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Invalid callback amount") from None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("9999999999.99") or amount.as_tuple().exponent < -2:
        raise ValidationError("Callback amount must be finite, positive, and have at most two decimal places")
    return amount


def _text(value, name, limit, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValidationError(f"Invalid {name}")
    return value


def handle_c2b_validation(*, tenant, payload):
    if not isinstance(payload, dict):
        raise ValidationError("Callback must be a JSON object")
    return {"ResultCode": 0, "ResultDesc": "Accepted"}


@transaction.atomic
def handle_c2b_confirmation(*, tenant, config, payload):
    """Internal processor: invoked only after inbox verification, never publicly."""
    if not isinstance(payload, dict):
        raise ValidationError("Callback must be a JSON object")
    transaction_id = _text(payload.get("TransID"), "TransID", 120)
    amount = _money(payload.get("TransAmount"))
    reference = _text(payload.get("BillRefNumber", ""), "BillRefNumber", 240, required=False)
    if payload.get("BusinessShortCode") is not None and str(payload["BusinessShortCode"]) != config.shortcode:
        raise ValidationError("Callback shortcode does not match this school")
    ingest_incoming_payment(
        user=config.system_user, tenant=tenant, payment_method=config.payment_method,
        amount=amount, external_reference=reference, external_transaction_id=transaction_id,
    )
    return {"ResultCode": 0, "ResultDesc": "Success"}


def _parse_stk(payload):
    try:
        callback = payload["Body"]["stkCallback"]
        checkout = _text(callback["CheckoutRequestID"], "CheckoutRequestID", 60)
        result = callback["ResultCode"]
        if isinstance(result, bool) or not isinstance(result, (int, str)):
            raise ValueError()
        result = int(result)
        metadata = {}
        if result == 0:
            for item in callback["CallbackMetadata"]["Item"]:
                name = item["Name"]
                if name in metadata:
                    raise ValidationError("Duplicate callback metadata")
                metadata[name] = item.get("Value")
            metadata["Amount"] = _money(metadata.get("Amount"))
            _text(metadata.get("MpesaReceiptNumber"), "MpesaReceiptNumber", 120)
        return callback, checkout, result, metadata
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValidationError("Malformed STK callback") from None


@transaction.atomic
def handle_stk_callback(*, tenant, config, payload, request_id=None):
    """One locked transition owns reconciliation and completion together."""
    callback, checkout, result_code, metadata = _parse_stk(payload)
    scope = {"pk": request_id} if request_id else {"checkout_request_id": checkout}
    try:
        request = MpesaStkPushRequest.objects.select_for_update().get(tenant=tenant, **scope)
    except MpesaStkPushRequest.DoesNotExist:
        raise ValidationError("Unknown checkout request; callback retained for recovery") from None
    if request.checkout_request_id and request.checkout_request_id != checkout:
        raise ValidationError("Callback checkout does not match this request")
    merchant = callback.get("MerchantRequestID", "")
    if request.merchant_request_id and merchant and request.merchant_request_id != merchant:
        raise ValidationError("Callback merchant request does not match")
    if request.status == MpesaStkPushStatus.COMPLETED:
        receipt = request.confirmed_receipt
        if receipt is None and request.incoming_payment_id:
            receipt = request.incoming_payment.external_transaction_id
        if result_code != 0 or metadata["MpesaReceiptNumber"] != receipt or metadata["Amount"] != request.amount:
            raise ValidationError("Conflicting callback for completed checkout")
        return request
    if request.status == MpesaStkPushStatus.FAILED:
        if result_code == 0 or str(result_code) != request.result_code:
            raise ValidationError("Conflicting callback for failed checkout")
        return request
    request.checkout_request_id = checkout
    if merchant:
        request.merchant_request_id = _text(merchant, "MerchantRequestID", 60)
    request.result_code = str(result_code)
    request.result_description = str(callback.get("ResultDesc", ""))[:240]
    if result_code != 0:
        request.status = MpesaStkPushStatus.FAILED
        request.save()
        return request
    if metadata["Amount"] != request.amount:
        raise ValidationError("Confirmed amount differs from requested amount; manual reconciliation required")
    if metadata.get("PhoneNumber") is not None and _normalize_msisdn(str(metadata["PhoneNumber"])) != request.phone_number:
        raise ValidationError("Callback phone number differs from requested phone")
    # Use known student identity; never fuzzy-match an STK collection to another
    # student because a reference changed since the request was initiated.
    receipt = metadata["MpesaReceiptNumber"]
    incoming = ingest_incoming_payment(
        user=config.system_user, tenant=tenant, payment_method=config.payment_method,
        amount=request.amount, external_reference="", external_transaction_id=receipt,
    )
    if incoming.status == ReconciliationStatus.UNMATCHED:
        incoming = match_incoming_payment(user=config.system_user, tenant=tenant, incoming=incoming, student=request.student)
    if incoming.status != ReconciliationStatus.MATCHED or incoming.matched_payment.student_id != request.student_id:
        raise ValidationError("Receipt is already resolved for a different account")
    request.status = MpesaStkPushStatus.COMPLETED
    request.confirmed_receipt = receipt
    request.incoming_payment = incoming
    try:
        with transaction.atomic():
            request.save()
    except IntegrityError as error:
        cause = error.__cause__
        constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
        if constraint == "unique_stk_receipt_per_tenant" or str(cause) == "UNIQUE constraint failed: finance_mpesastkpushrequest.tenant_id, finance_mpesastkpushrequest.confirmed_receipt":
            raise ValidationError("Receipt is already linked to another checkout") from error
        raise
    return request


@transaction.atomic
def verify_mpesa_callback(*, user, tenant, callback_id, evidence):
    require_permission(user=user, tenant=tenant, permission="finance.mpesa.callback.verify")
    evidence = _text(evidence, "verification reference", 240)
    try:
        callback = MpesaCallbackLog.objects.select_for_update().get(tenant=tenant, pk=callback_id)
    except MpesaCallbackLog.DoesNotExist:
        raise ValidationError("Callback is not available in this school") from None
    if callback.status == MpesaCallbackStatus.REJECTED:
        raise ValidationError("Rejected callback cannot be verified")
    if callback.verified_at is None:
        callback.verified_by = user
        callback.verified_at = timezone.now()
        callback.verification_reference = evidence
        callback.save(update_fields=["verified_by", "verified_at", "verification_reference"])
        record_activity(tenant=tenant, actor=user, action="mpesa.callback_verified",
                        resource_type="mpesa_callback", resource_id=str(callback.pk),
                        metadata={"reference": evidence})
    return callback


def process_mpesa_callback(*, user, tenant, callback_id):
    require_permission(user=user, tenant=tenant, permission="finance.mpesa.callback.process")
    unexpected = None
    with transaction.atomic():
        try:
            callback = MpesaCallbackLog.objects.select_for_update().get(tenant=tenant, pk=callback_id)
        except MpesaCallbackLog.DoesNotExist:
            raise ValidationError("Callback is not available in this school") from None
        if callback.status == MpesaCallbackStatus.PROCESSED:
            return callback
        if callback.verified_at is None or callback.status == MpesaCallbackStatus.REJECTED:
            raise ValidationError("Callback requires independent verification before processing")
        callback.attempts += 1
        try:
            with transaction.atomic():
                config = TenantMpesaConfiguration.objects.filter(tenant=tenant, is_active=True).first()
                if config is None:
                    raise ValidationError("Active M-Pesa configuration is required")
                if callback.callback_type == MpesaCallbackType.STK_CALLBACK:
                    handle_stk_callback(tenant=tenant, config=config, payload=callback.raw_payload, request_id=callback.request_id)
                elif callback.callback_type == MpesaCallbackType.C2B_CONFIRMATION:
                    handle_c2b_confirmation(tenant=tenant, config=config, payload=callback.raw_payload)
                else:
                    handle_c2b_validation(tenant=tenant, payload=callback.raw_payload)
        except Exception as error:
            callback.status = MpesaCallbackStatus.FAILED
            if isinstance(error, ValidationError):
                callback.last_error = "; ".join(error.messages)[:240]
            else:
                callback.last_error = "Unexpected processing failure; investigate before retrying"
                unexpected = error
        else:
            callback.status = MpesaCallbackStatus.PROCESSED
            callback.processed_at = timezone.now()
            callback.last_error = ""
        callback.save(update_fields=["attempts", "status", "processed_at", "last_error"])
    if unexpected is not None:
        raise unexpected
    return callback


@transaction.atomic
def reject_mpesa_callback(*, user, tenant, callback_id, reason):
    require_permission(user=user, tenant=tenant, permission="finance.mpesa.callback.verify")
    reason = _text(reason, "reason", 240)
    callback = MpesaCallbackLog.objects.select_for_update().filter(tenant=tenant, pk=callback_id).first()
    if callback is None or callback.status == MpesaCallbackStatus.PROCESSED:
        raise ValidationError("Callback cannot be rejected")
    callback.status = MpesaCallbackStatus.REJECTED
    callback.last_error = reason
    callback.save(update_fields=["status", "last_error"])
    record_activity(tenant=tenant, actor=user, action="mpesa.callback_rejected",
                    resource_type="mpesa_callback", resource_id=str(callback.pk), metadata={"reason": reason})
    return callback


def query_stk_request(*, user, tenant, request):
    require_permission(user=user, tenant=tenant, permission="finance.mpesa.stk_push.query")
    request = MpesaStkPushRequest.objects.for_tenant(tenant).filter(pk=request.pk).first()
    if request is None or not request.checkout_request_id:
        raise ValidationError("Checkout ID is not yet known; inspect the retained callback inbox")
    config = TenantMpesaConfiguration.objects.get(tenant=tenant, is_active=True)
    try:
        result = MpesaClient(config).query_stk(checkout_request_id=request.checkout_request_id)
    except MpesaApiError as error:
        raise ValidationError(str(error)) from error
    # A status response is not a verified receipt/amount. Store evidence for
    # recovery without inventing a payment or changing a terminal state.
    MpesaStkPushRequest.objects.filter(tenant=tenant, pk=request.pk).update(provider_query=result, queried_at=timezone.now())
    request.refresh_from_db()
    return request


@transaction.atomic
def identify_stk_request(*, user, tenant, request_id, checkout_request_id, merchant_request_id, evidence):
    """Recover provider identifiers from an independently checked record.

    This never sends a second prompt or posts money. It makes a lost-response
    request queryable and permits retained legacy callbacks to find it.
    """
    require_permission(user=user, tenant=tenant, permission="finance.mpesa.stk_push.reconcile")
    checkout_request_id = _text(checkout_request_id, "CheckoutRequestID", 60)
    merchant_request_id = _text(merchant_request_id, "MerchantRequestID", 60)
    evidence = _text(evidence, "verification reference", 240)
    request = MpesaStkPushRequest.objects.select_for_update().filter(tenant=tenant, pk=request_id).first()
    if request is None or request.status not in (MpesaStkPushStatus.INITIATING, MpesaStkPushStatus.UNKNOWN):
        raise ValidationError("Only an unresolved initiation can be identified")
    if request.checkout_request_id and request.checkout_request_id != checkout_request_id:
        raise ValidationError("Checkout ID conflicts with the recorded request")
    try:
        with transaction.atomic():
            request.checkout_request_id = checkout_request_id
            request.merchant_request_id = merchant_request_id
            request.status = MpesaStkPushStatus.PENDING
            request.save(update_fields=["checkout_request_id", "merchant_request_id", "status"])
    except IntegrityError as error:
        cause = error.__cause__
        constraint = getattr(getattr(cause, "diag", None), "constraint_name", None)
        if constraint == "unique_stk_checkout_request_per_tenant" or str(cause) == "UNIQUE constraint failed: finance_mpesastkpushrequest.tenant_id, finance_mpesastkpushrequest.checkout_request_id":
            raise ValidationError("Checkout ID is already linked to another request") from error
        raise
    record_activity(tenant=tenant, actor=user, action="mpesa.stk_identified",
                    resource_type="mpesa_stk_push_request", resource_id=str(request.pk), metadata={"reference": evidence})
    return request

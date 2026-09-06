import re
import secrets
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.activity.services import record_activity
from apps.tenancy.models import Membership, Role, User
from apps.tenancy.services import require_permission

from .models import (
    MpesaCallbackLog,
    MpesaCallbackType,
    MpesaStkPushRequest,
    MpesaStkPushStatus,
    PaymentMethod,
    ReconciliationStatus,
    TenantMpesaConfiguration,
    validate_same_tenant,
)
from .mpesa_client import MpesaApiError, MpesaClient
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


def configure_mpesa_gateway(*, user, tenant, environment, shortcode, consumer_key, consumer_secret, passkey):
    """Idempotent: safe to call again to rotate credentials -- rotating
    credentials never touches callback_token (see rotate_mpesa_callback_token
    for the separate, explicit "the token itself was compromised" action).
    Creates the tenant's M-Pesa PaymentMethod and system user/role/
    membership on first call only.
    """
    require_permission(user=user, tenant=tenant, permission="finance.mpesa.configure")
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


def rotate_mpesa_callback_token(*, user, tenant):
    """Explicit, separate action for "the callback URL was compromised" --
    never done implicitly by configure_mpesa_gateway. Safaricom's registered
    URLs must be updated to match after calling this.
    """
    require_permission(user=user, tenant=tenant, permission="finance.mpesa.configure")
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


def initiate_stk_push(*, user, tenant, student, phone_number, amount, invoice=None, transaction_desc="School fees"):
    require_permission(user=user, tenant=tenant, permission="finance.mpesa.stk_push.initiate")
    if amount <= 0:
        raise ValidationError("STK push amount must be greater than zero")
    validate_same_tenant(tenant=tenant, student=student, **({"invoice": invoice} if invoice else {}))
    normalized_phone = _normalize_msisdn(phone_number)
    try:
        config = TenantMpesaConfiguration.objects.get(tenant=tenant, is_active=True)
    except TenantMpesaConfiguration.DoesNotExist as error:
        raise ValidationError("M-Pesa is not configured for this school") from error

    # No transaction.atomic() here: the HTTP call must complete before any
    # database write begins, never the reverse (execution-model.md rule 4 --
    # external HTTP never runs while a database lock/transaction is held).
    callback_url = f"{settings.PUBLIC_BASE_URL}/api/v1/finance/mpesa/{config.callback_token}/stk/callback/"
    try:
        response = MpesaClient(config).stk_push(
            phone_number=normalized_phone, amount=amount, account_reference=student.admission_number,
            transaction_desc=transaction_desc, callback_url=callback_url,
        )
    except MpesaApiError as error:
        raise ValidationError(str(error)) from error

    return MpesaStkPushRequest.objects.create(
        tenant=tenant, student=student, invoice=invoice, phone_number=normalized_phone, amount=amount,
        account_reference=student.admission_number,
        merchant_request_id=response["MerchantRequestID"], checkout_request_id=response["CheckoutRequestID"],
    )


def log_mpesa_callback(*, tenant, callback_type, payload, provider_transaction_id=""):
    """Always call this first, before any processing, and never inside the
    same transaction.atomic() as processing -- a plain .create() outside an
    explicit atomic() block commits immediately, so this durably records
    that a callback arrived even if processing afterward fails.
    """
    return MpesaCallbackLog.objects.create(
        tenant=tenant, callback_type=callback_type, raw_payload=payload,
        provider_transaction_id=provider_transaction_id,
    )


def handle_c2b_validation(*, tenant, payload):
    # Validation doesn't move money -- Safaricom sends a separate
    # confirmation regardless of this response -- so it doesn't need the
    # strict no-swallow protocol that confirmation and the STK callback do.
    return {"ResultCode": 0, "ResultDesc": "Accepted"}  # v1: accept everything structurally sane; no business-rule rejection yet


def handle_c2b_confirmation(*, tenant, config, payload):
    """Caller (the view) must have already called log_mpesa_callback and
    must let any exception from this function propagate uncaught. Requires
    TransID and TransAmount; a confirmation claiming success without them
    is malformed and must not manufacture financial identity.
    """
    transaction_id = payload.get("TransID")
    raw_amount = payload.get("TransAmount")
    if not transaction_id or raw_amount is None:
        raise ValidationError("C2B confirmation is missing TransID or TransAmount")
    ingest_incoming_payment(
        user=config.system_user, tenant=tenant, payment_method=config.payment_method,
        amount=Decimal(str(raw_amount)), external_reference=payload.get("BillRefNumber", ""),
        external_transaction_id=str(transaction_id),
    )
    return {"ResultCode": 0, "ResultDesc": "Success"}


def _flatten_callback_metadata(items):
    return {item["Name"]: item.get("Value") for item in items}


def handle_stk_callback(*, tenant, config, payload):
    """Caller must have already called log_mpesa_callback; no exception
    swallowing here either.
    """
    stk_callback = payload["Body"]["stkCallback"]
    checkout_request_id = stk_callback["CheckoutRequestID"]
    result_code = stk_callback["ResultCode"]

    with transaction.atomic():
        try:
            stk_request = MpesaStkPushRequest.objects.select_for_update().get(
                tenant=tenant, checkout_request_id=checkout_request_id,
            )
        except MpesaStkPushRequest.DoesNotExist:
            return  # unknown/stale checkout id -- nothing to reconcile against
        if stk_request.status != MpesaStkPushStatus.PENDING:
            return  # already resolved; a Safaricom retry of an old callback is a safe no-op
        if result_code != 0:
            stk_request.status = MpesaStkPushStatus.FAILED
            stk_request.result_code = str(result_code)
            stk_request.result_description = stk_callback.get("ResultDesc", "")
            stk_request.save(update_fields=["status", "result_code", "result_description"])
            return

    # Past this point result_code == 0 ("success") and the request was
    # still PENDING. A "successful" callback missing its receipt or amount
    # is malformed and must be rejected, not turned into money with a
    # fabricated identity.
    metadata = _flatten_callback_metadata(stk_callback.get("CallbackMetadata", {}).get("Item", []))
    receipt_number = metadata.get("MpesaReceiptNumber")
    raw_amount = metadata.get("Amount")
    if not receipt_number or raw_amount is None:
        raise ValidationError("STK callback reported success but is missing Amount or MpesaReceiptNumber")
    amount = Decimal(str(raw_amount))
    if amount != stk_request.amount:
        # Trust what Safaricom says was actually collected, but make the
        # discrepancy visible rather than silently accepting or rejecting it.
        record_activity(
            tenant=tenant, actor=config.system_user, action="mpesa.stk_amount_mismatch",
            resource_type="mpesa_stk_push_request", resource_id=str(stk_request.id),
            metadata={"requested": str(stk_request.amount), "confirmed": str(amount)},
        )

    incoming = ingest_incoming_payment(
        user=config.system_user, tenant=tenant, payment_method=config.payment_method,
        amount=amount, external_reference=stk_request.account_reference,
        external_transaction_id=str(receipt_number),
    )
    if incoming.status != ReconciliationStatus.MATCHED:
        # We know the student for certain (this was initiated against a
        # specific student) -- don't leave it to reference-recognition
        # guesswork when we don't have to.
        incoming = match_incoming_payment(user=config.system_user, tenant=tenant, incoming=incoming, student=stk_request.student)

    with transaction.atomic():
        locked = MpesaStkPushRequest.objects.select_for_update().get(tenant=tenant, pk=stk_request.pk)
        locked.status = MpesaStkPushStatus.COMPLETED
        locked.result_code = str(result_code)
        locked.result_description = stk_callback.get("ResultDesc", "")
        locked.incoming_payment = incoming
        locked.save(update_fields=["status", "result_code", "result_description", "incoming_payment"])

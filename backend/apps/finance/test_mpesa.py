from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings

from apps.academics.models import AcademicLevel, AcademicYear
from apps.activity.models import ActivityEvent
from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import (
    FeeCategory,
    FeeItem,
    IncomingPayment,
    MpesaStkPushRequest,
    MpesaStkPushStatus,
    NumberSeries,
    Payment,
    PaymentMethod,
    ReconciliationStatus,
    TenantMpesaConfiguration,
)
from .mpesa_client import MpesaApiError
from .mpesa_services import (
    SYSTEM_ROLE_PERMISSIONS,
    _normalize_msisdn,
    configure_mpesa_gateway,
    handle_c2b_confirmation,
    handle_stk_callback,
    initiate_stk_push,
    log_mpesa_callback,
    resolve_mpesa_tenant,
    rotate_mpesa_callback_token,
)
from .models import MpesaCallbackType
from .services import (
    add_fee_structure_line,
    allocate_payment,
    approve_fee_structure,
    assign_fee_structure,
    create_fee_structure,
    generate_invoice,
    issue_invoice,
)


class MpesaGatewayConfigurationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.user = User.objects.create_user(username="admin", password="secret")
        self.role = Role.objects.create(tenant=self.tenant, name="Admin", permissions=["finance.mpesa.configure"])
        Membership.objects.create(tenant=self.tenant, user=self.user, role=self.role)

    def _configure(self, **overrides):
        kwargs = dict(
            user=self.user, tenant=self.tenant, environment="SANDBOX", shortcode="600000",
            consumer_key="key-1", consumer_secret="secret-1", passkey="passkey-1",
        )
        kwargs.update(overrides)
        return configure_mpesa_gateway(**kwargs)

    def test_first_call_creates_system_user_role_membership_and_payment_method(self):
        config = self._configure()

        self.assertTrue(config.system_user.username.startswith("mpesa-gateway-"))
        self.assertFalse(config.system_user.has_usable_password())
        membership = Membership.objects.get(tenant=self.tenant, user=config.system_user)
        self.assertCountEqual(membership.role.permissions, SYSTEM_ROLE_PERMISSIONS)
        self.assertEqual(config.payment_method.code, "MPESA")
        self.assertTrue(config.callback_token)

    def test_second_call_rotates_credentials_without_duplicating_system_user_or_token(self):
        first = self._configure()
        second = self._configure(consumer_key="key-2", consumer_secret="secret-2", passkey="passkey-2")

        self.assertEqual(first.system_user_id, second.system_user_id)
        self.assertEqual(first.callback_token, second.callback_token)
        self.assertEqual(TenantMpesaConfiguration.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(User.objects.filter(username__startswith="mpesa-gateway-").count(), 1)
        self.assertEqual(second.consumer_key, "key-2")

    def test_configure_requires_its_own_permission(self):
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])

        with self.assertRaises(ValidationError):
            self._configure()

    def test_rotate_callback_token_changes_token_and_invalidates_the_old_one(self):
        config = self._configure()
        old_token = config.callback_token

        rotated = rotate_mpesa_callback_token(user=self.user, tenant=self.tenant)

        self.assertNotEqual(rotated.callback_token, old_token)
        with self.assertRaises(TenantMpesaConfiguration.DoesNotExist):
            resolve_mpesa_tenant(callback_token=old_token)
        self.assertEqual(resolve_mpesa_tenant(callback_token=rotated.callback_token).tenant_id, self.tenant.id)


class NormalizeMsisdnTests(TestCase):
    def test_accepts_recognized_formats(self):
        for raw in ("0712345678", "254712345678", "+254712345678"):
            self.assertEqual(_normalize_msisdn(raw), "254712345678")

    def test_rejects_unrecognized_input(self):
        for raw in ("12345", "not-a-phone", "254212345678"):
            with self.assertRaises(ValidationError):
                _normalize_msisdn(raw)


class StkPushTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.user = User.objects.create_user(username="bursar", password="secret")
        self.role = Role.objects.create(
            tenant=self.tenant, name="Bursar",
            permissions=["finance.mpesa.configure", "finance.mpesa.stk_push.initiate"],
        )
        Membership.objects.create(tenant=self.tenant, user=self.user, role=self.role)
        self.student = Student.objects.create(tenant=self.tenant, admission_number="ADM-001", first_name="Amina", last_name="Otieno")
        self.config = configure_mpesa_gateway(
            user=self.user, tenant=self.tenant, environment="SANDBOX", shortcode="600000",
            consumer_key="key", consumer_secret="secret", passkey="passkey",
        )

    @patch("apps.finance.mpesa_services.MpesaClient")
    def test_initiate_stk_push_normalizes_phone_and_persists_request(self, mock_client_cls):
        mock_client_cls.return_value.stk_push.return_value = {
            "MerchantRequestID": "merchant-1", "CheckoutRequestID": "checkout-1",
        }

        stk_request = initiate_stk_push(
            user=self.user, tenant=self.tenant, student=self.student,
            phone_number="0712345678", amount=Decimal("1000.00"),
        )

        self.assertEqual(stk_request.phone_number, "254712345678")
        self.assertEqual(stk_request.checkout_request_id, "checkout-1")
        self.assertEqual(stk_request.status, MpesaStkPushStatus.PENDING)
        call_kwargs = mock_client_cls.return_value.stk_push.call_args.kwargs
        self.assertEqual(call_kwargs["phone_number"], "254712345678")
        self.assertEqual(call_kwargs["account_reference"], "ADM-001")

    @patch("apps.finance.mpesa_services.MpesaClient")
    def test_initiate_stk_push_api_error_preserves_unknown_request(self, mock_client_cls):
        mock_client_cls.return_value.stk_push.side_effect = MpesaApiError("network down")

        request = initiate_stk_push(user=self.user, tenant=self.tenant, student=self.student, phone_number="0712345678", amount=Decimal("1000.00"))
        self.assertEqual(request.status, MpesaStkPushStatus.UNKNOWN)
        self.assertEqual(MpesaStkPushRequest.objects.count(), 1)

    def test_initiate_stk_push_without_configuration_is_rejected(self):
        other_tenant = Tenant.objects.create(name="School B", slug="school-b")
        other_user = User.objects.create_user(username="other", password="secret")
        other_role = Role.objects.create(tenant=other_tenant, name="Bursar", permissions=["finance.mpesa.stk_push.initiate"])
        Membership.objects.create(tenant=other_tenant, user=other_user, role=other_role)
        other_student = Student.objects.create(tenant=other_tenant, admission_number="ADM-900", first_name="X", last_name="Y")

        with self.assertRaises(ValidationError):
            initiate_stk_push(user=other_user, tenant=other_tenant, student=other_student, phone_number="0712345678", amount=Decimal("1000.00"))


class MpesaCallbackHandlingTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.user = User.objects.create_user(username="bursar", password="secret")
        self.role = Role.objects.create(
            tenant=self.tenant, name="Bursar",
            permissions=["finance.mpesa.configure", "finance.mpesa.stk_push.initiate", "finance.fee_structure.create", "finance.fee_structure.edit", "finance.fee_structure.approve", "finance.invoice.create", "finance.invoice.issue"],
        )
        Membership.objects.create(tenant=self.tenant, user=self.user, role=self.role)
        self.student = Student.objects.create(tenant=self.tenant, admission_number="ADM-001", first_name="Amina", last_name="Otieno")
        self.config = configure_mpesa_gateway(
            user=self.user, tenant=self.tenant, environment="SANDBOX", shortcode="600000",
            consumer_key="key", consumer_secret="secret", passkey="passkey",
        )
        NumberSeries.objects.create(tenant=self.tenant, document_type="INVOICE", prefix="INV-2026-", padding=6)
        NumberSeries.objects.create(tenant=self.tenant, document_type="RECEIPT", prefix="RCT-2026-", padding=6)

        year = AcademicYear.objects.create(tenant=self.tenant, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        level = AcademicLevel.objects.create(tenant=self.tenant, name="Grade 8", code="G8", sequence=8)
        category = FeeCategory.objects.create(tenant=self.tenant, name="Tuition", code="TUITION")
        item = FeeItem.objects.create(tenant=self.tenant, category=category, name="Tuition fee", code="TUITION")
        structure = create_fee_structure(user=self.user, tenant=self.tenant, name="Grade 8 2026", academic_year=year, academic_level=level)
        add_fee_structure_line(user=self.user, tenant=self.tenant, fee_structure=structure, fee_item=item, amount=Decimal("50000.00"))
        approve_fee_structure(user=self.user, tenant=self.tenant, fee_structure=structure)
        assignment = assign_fee_structure(user=self.user, tenant=self.tenant, student=self.student, fee_structure=structure)
        self.invoice = generate_invoice(user=self.user, tenant=self.tenant, assignment=assignment)
        issue_invoice(user=self.user, tenant=self.tenant, invoice=self.invoice)

    # --- C2B confirmation ---

    def test_c2b_confirmation_creates_incoming_payment_and_payment(self):
        payload = {"TransID": "QGH7XYZ001", "TransAmount": "50000", "BillRefNumber": self.student.admission_number}

        handle_c2b_confirmation(tenant=self.tenant, config=self.config, payload=payload)

        incoming = IncomingPayment.objects.get(tenant=self.tenant, external_transaction_id="QGH7XYZ001")
        self.assertEqual(incoming.status, ReconciliationStatus.MATCHED)
        self.assertEqual(incoming.matched_payment.student_id, self.student.id)

    def test_c2b_confirmation_missing_fields_raises_and_creates_nothing(self):
        with self.assertRaises(ValidationError):
            handle_c2b_confirmation(tenant=self.tenant, config=self.config, payload={"BillRefNumber": "ADM-001"})

        self.assertEqual(IncomingPayment.objects.count(), 0)

    def test_c2b_confirmation_replay_is_idempotent(self):
        payload = {"TransID": "QGH7XYZ002", "TransAmount": "1000", "BillRefNumber": ""}

        handle_c2b_confirmation(tenant=self.tenant, config=self.config, payload=payload)
        handle_c2b_confirmation(tenant=self.tenant, config=self.config, payload=payload)

        self.assertEqual(IncomingPayment.objects.filter(external_transaction_id="QGH7XYZ002").count(), 1)

    # --- STK callback ---

    def _stk_request(self, amount="50000.00"):
        with patch("apps.finance.mpesa_services.MpesaClient") as mock_client_cls:
            mock_client_cls.return_value.stk_push.return_value = {"MerchantRequestID": "merchant-x", "CheckoutRequestID": "checkout-x"}
            return initiate_stk_push(user=self.user, tenant=self.tenant, student=self.student, phone_number="0712345678", amount=Decimal(amount))

    def _stk_callback_payload(self, checkout_request_id, result_code=0, amount="50000", receipt="NLJ7RT61SV", result_desc="The service request is processed successfully."):
        item = [{"Name": "Amount", "Value": amount}, {"Name": "MpesaReceiptNumber", "Value": receipt}, {"Name": "TransactionDate", "Value": 20260101120000}, {"Name": "PhoneNumber", "Value": 254712345678}]
        body = {"ResultCode": result_code, "ResultDesc": result_desc, "CheckoutRequestID": checkout_request_id, "MerchantRequestID": "merchant-x"}
        if result_code == 0:
            body["CallbackMetadata"] = {"Item": item}
        return {"Body": {"stkCallback": body}}

    def test_stk_callback_success_force_matches_known_student_and_completes(self):
        # Reference used at initiation is the admission number, so this
        # would also auto-match via 5B's recognition -- but the force-match
        # path is exercised regardless via the explicit status check.
        stk_request = self._stk_request()
        payload = self._stk_callback_payload(stk_request.checkout_request_id)

        handle_stk_callback(tenant=self.tenant, config=self.config, payload=payload)

        stk_request.refresh_from_db()
        self.assertEqual(stk_request.status, MpesaStkPushStatus.COMPLETED)
        self.assertIsNotNone(stk_request.incoming_payment)
        self.assertEqual(stk_request.incoming_payment.matched_payment.student_id, self.student.id)
        self.assertEqual(Payment.objects.filter(idempotency_key=f"incoming-payment:{stk_request.incoming_payment_id}").count(), 1)

    def test_stk_callback_failure_marks_failed_and_creates_no_payment(self):
        stk_request = self._stk_request()
        payload = self._stk_callback_payload(stk_request.checkout_request_id, result_code=1032, result_desc="Request cancelled by user")

        handle_stk_callback(tenant=self.tenant, config=self.config, payload=payload)

        stk_request.refresh_from_db()
        self.assertEqual(stk_request.status, MpesaStkPushStatus.FAILED)
        self.assertIsNone(stk_request.incoming_payment)
        self.assertEqual(Payment.objects.count(), 0)

    def test_stk_callback_unknown_checkout_id_is_a_noop(self):
        with self.assertRaisesMessage(ValidationError, "Unknown checkout"):
            handle_stk_callback(tenant=self.tenant, config=self.config, payload=self._stk_callback_payload("does-not-exist"))
        self.assertEqual(Payment.objects.count(), 0)

    def test_stk_callback_replay_after_completion_is_a_noop(self):
        stk_request = self._stk_request()
        payload = self._stk_callback_payload(stk_request.checkout_request_id)
        handle_stk_callback(tenant=self.tenant, config=self.config, payload=payload)

        handle_stk_callback(tenant=self.tenant, config=self.config, payload=payload)  # Safaricom retry

        self.assertEqual(Payment.objects.count(), 1)

    def test_stk_callback_missing_receipt_or_amount_is_rejected(self):
        stk_request = self._stk_request()
        payload = {"Body": {"stkCallback": {
            "ResultCode": 0, "ResultDesc": "ok", "CheckoutRequestID": stk_request.checkout_request_id,
            "CallbackMetadata": {"Item": [{"Name": "Amount", "Value": "50000"}]},  # no MpesaReceiptNumber
        }}}

        with self.assertRaises(ValidationError):
            handle_stk_callback(tenant=self.tenant, config=self.config, payload=payload)

        self.assertEqual(Payment.objects.count(), 0)
        stk_request.refresh_from_db()
        self.assertEqual(stk_request.status, MpesaStkPushStatus.PENDING)

    def test_stk_callback_amount_mismatch_requires_reconciliation(self):
        request = self._stk_request()
        with self.assertRaisesMessage(ValidationError, "Confirmed amount differs"):
            handle_stk_callback(tenant=self.tenant, config=self.config,
                                payload=self._stk_callback_payload(request.checkout_request_id, amount="1000"))
        request.refresh_from_db()
        self.assertEqual(request.status, MpesaStkPushStatus.PENDING)
        self.assertEqual(Payment.objects.count(), 0)

    # --- log_mpesa_callback ---

    def test_log_mpesa_callback_persists_raw_payload(self):
        log = log_mpesa_callback(tenant=self.tenant, callback_type=MpesaCallbackType.C2B_CONFIRMATION, payload={"TransID": "X"}, provider_transaction_id="X")
        self.assertEqual(log.raw_payload, {"TransID": "X"})


class EncryptedCharFieldTests(TestCase):
    def test_round_trips_through_the_orm(self):
        tenant = Tenant.objects.create(name="School A", slug="school-a")
        user = User.objects.create_user(username="admin", password="secret")
        role = Role.objects.create(tenant=tenant, name="Admin", permissions=["finance.mpesa.configure"])
        Membership.objects.create(tenant=tenant, user=user, role=role)
        config = configure_mpesa_gateway(
            user=user, tenant=tenant, environment="SANDBOX", shortcode="600000",
            consumer_key="my-consumer-key", consumer_secret="my-consumer-secret", passkey="my-passkey",
        )

        reloaded = TenantMpesaConfiguration.objects.get(pk=config.pk)
        self.assertEqual(reloaded.consumer_key, "my-consumer-key")
        self.assertEqual(reloaded.consumer_secret, "my-consumer-secret")
        self.assertEqual(reloaded.passkey, "my-passkey")

        with connection.cursor() as cursor:
            cursor.execute("SELECT consumer_key FROM finance_tenantmpesaconfiguration WHERE id = %s", [config.pk])
            raw_value = cursor.fetchone()[0]
        self.assertNotEqual(raw_value, "my-consumer-key")
        self.assertNotIn("my-consumer-key", raw_value)

    def test_wrong_key_raises_a_clear_error_rather_than_a_silent_wrong_answer(self):
        tenant = Tenant.objects.create(name="School A", slug="school-a")
        user = User.objects.create_user(username="admin", password="secret")
        role = Role.objects.create(tenant=tenant, name="Admin", permissions=["finance.mpesa.configure"])
        Membership.objects.create(tenant=tenant, user=user, role=role)
        # Encrypted with the real settings.FIELD_ENCRYPTION_KEY (the dev
        # default, since this test doesn't override it).
        config = configure_mpesa_gateway(
            user=user, tenant=tenant, environment="SANDBOX", shortcode="600000",
            consumer_key="my-consumer-key", consumer_secret="my-consumer-secret", passkey="my-passkey",
        )

        with override_settings(FIELD_ENCRYPTION_KEY="wGLgCGwaMlA56k101LdsO9JkZXjnPLiqwVdYw2UOX_o="):
            with self.assertRaises(ValueError):
                TenantMpesaConfiguration.objects.get(pk=config.pk).consumer_key

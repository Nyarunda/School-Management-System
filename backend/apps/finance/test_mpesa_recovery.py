from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from . import test_mpesa as fixtures
from .models import (MpesaCallbackLog, MpesaCallbackType, MpesaStkPushRequest, Payment,
                     IncomingPayment, TenantMpesaConfiguration)
from .mpesa_client import MpesaApiError, MpesaClient
from .mpesa_services import (configure_mpesa_gateway, handle_stk_callback, initiate_stk_push,
    log_mpesa_callback, process_mpesa_callback, query_stk_request, verify_mpesa_callback)


class GatewayRecoveryTests(TestCase):
    setUp = fixtures.MpesaCallbackHandlingTests.setUp
    _stk_request = fixtures.MpesaCallbackHandlingTests._stk_request
    _stk_callback_payload = fixtures.MpesaCallbackHandlingTests._stk_callback_payload

    def grant_recovery(self):
        self.role.permissions += ["finance.mpesa.callback.verify", "finance.mpesa.callback.process",
            "finance.mpesa.callback.view", "finance.mpesa.stk_push.view", "finance.mpesa.stk_push.query"]
        self.role.save(update_fields=["permissions"])

    def verified_callback(self, request, payload=None):
        self.grant_recovery()
        item = log_mpesa_callback(tenant=self.tenant, callback_type=MpesaCallbackType.STK_CALLBACK,
            payload=payload or self._stk_callback_payload(request.checkout_request_id))
        return verify_mpesa_callback(user=self.user, tenant=self.tenant, callback_id=item.id,
                                     evidence="Provider statement independently checked: TEST-001")

    def test_same_key_does_not_send_a_second_prompt(self):
        with patch("apps.finance.mpesa_services.MpesaClient") as client:
            client.return_value.stk_push.return_value = {"MerchantRequestID": "m", "CheckoutRequestID": "c"}
            kwargs = dict(user=self.user, tenant=self.tenant, student=self.student,
                          phone_number="0712345678", amount=Decimal("1000"), idempotency_key="same")
            first = initiate_stk_push(**kwargs)
            second = initiate_stk_push(**kwargs)
            self.assertEqual(first.pk, second.pk)
            self.assertEqual(client.return_value.stk_push.call_count, 1)
            with self.assertRaisesMessage(ValidationError, "different STK details"):
                initiate_stk_push(**{**kwargs, "amount": Decimal("1001")})

    def test_network_failure_keeps_intent_and_retry_does_not_resend(self):
        with patch("apps.finance.mpesa_services.MpesaClient") as client:
            client.return_value.stk_push.side_effect = MpesaApiError("timeout")
            kwargs = dict(user=self.user, tenant=self.tenant, student=self.student,
                          phone_number="0712345678", amount=Decimal("1000"), idempotency_key="unknown")
            first = initiate_stk_push(**kwargs)
            self.assertEqual(first.status, "UNKNOWN")
            self.assertEqual(initiate_stk_push(**kwargs).pk, first.pk)
            self.assertEqual(client.return_value.stk_push.call_count, 1)

    def test_local_intent_exists_when_callback_arrives_during_http_call(self):
        def send(**kwargs):
            request = MpesaStkPushRequest.objects.get()
            self.assertEqual(request.status, "INITIATING")
            self.assertIn(str(request.pk), kwargs["callback_url"])
            payload = self._stk_callback_payload("early-checkout", amount="1000")
            log_mpesa_callback(tenant=self.tenant, callback_type=MpesaCallbackType.STK_CALLBACK,
                              payload=payload, request_id=request.pk)
            raise MpesaApiError("response lost")
        with patch("apps.finance.mpesa_services.MpesaClient") as client:
            client.return_value.stk_push.side_effect = send
            request = initiate_stk_push(user=self.user, tenant=self.tenant, student=self.student,
                phone_number="0712345678", amount=Decimal("1000"), idempotency_key="early")
        self.grant_recovery()
        item = MpesaCallbackLog.objects.get()
        verify_mpesa_callback(user=self.user, tenant=self.tenant, callback_id=item.pk, evidence="Statement TEST-EARLY")
        result = process_mpesa_callback(user=self.user, tenant=self.tenant, callback_id=item.pk)
        self.assertEqual(result.status, "PROCESSED", result.last_error)
        request.refresh_from_db()
        self.assertEqual(request.checkout_request_id, "early-checkout")
        self.assertEqual(request.status, "COMPLETED")
        self.assertEqual(Payment.objects.count(), 1)

    def test_conflicting_receipt_does_not_post_twice(self):
        request = self._stk_request()
        handle_stk_callback(tenant=self.tenant, config=self.config,
                            payload=self._stk_callback_payload(request.checkout_request_id))
        with self.assertRaisesMessage(ValidationError, "Conflicting callback"):
            handle_stk_callback(tenant=self.tenant, config=self.config,
                payload=self._stk_callback_payload(request.checkout_request_id, receipt="different"))
        self.assertEqual(Payment.objects.count(), 1)

    def test_unverified_callbacks_cannot_post_and_replay_is_idempotent(self):
        request = self._stk_request()
        self.grant_recovery()
        item = log_mpesa_callback(tenant=self.tenant, callback_type=MpesaCallbackType.STK_CALLBACK,
                                 payload=self._stk_callback_payload(request.checkout_request_id))
        with self.assertRaisesMessage(ValidationError, "independent verification"):
            process_mpesa_callback(user=self.user, tenant=self.tenant, callback_id=item.pk)
        self.assertEqual(Payment.objects.count(), 0)
        verify_mpesa_callback(user=self.user, tenant=self.tenant, callback_id=item.pk, evidence="Statement TEST-REPLAY")
        for _ in range(2):
            result = process_mpesa_callback(user=self.user, tenant=self.tenant, callback_id=item.pk)
            self.assertEqual(result.status, "PROCESSED")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(Payment.objects.count(), 1)

    def test_posting_failure_is_retained_and_all_financial_writes_roll_back(self):
        request = self._stk_request()
        item = self.verified_callback(request)
        original_save = MpesaStkPushRequest.save
        def fail_completion(instance, *args, **kwargs):
            if instance.status == "COMPLETED":
                raise RuntimeError("simulated final-write failure")
            return original_save(instance, *args, **kwargs)
        with patch.object(MpesaStkPushRequest, "save", fail_completion):
            with self.assertRaises(RuntimeError):
                process_mpesa_callback(user=self.user, tenant=self.tenant, callback_id=item.pk)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(IncomingPayment.objects.count(), 0)
        item.refresh_from_db()
        self.assertEqual(item.status, "FAILED")
        self.assertEqual(item.attempts, 1)
        self.assertEqual(process_mpesa_callback(user=self.user, tenant=self.tenant, callback_id=item.pk).status, "PROCESSED")

    def test_malformed_callback_is_retained_as_failed(self):
        request = self._stk_request()
        for payload in ({}, {"Body": []}, {"Body": {"stkCallback": {"ResultCode": False}}}):
            item = self.verified_callback(request, payload={"bad": payload})
            result = process_mpesa_callback(user=self.user, tenant=self.tenant, callback_id=item.pk)
            self.assertEqual(result.status, "FAILED")
        self.assertEqual(Payment.objects.count(), 0)

    def test_gateway_setup_rolls_back_all_created_records(self):
        from apps.tenancy.models import Membership, Role, Tenant, User
        other = Tenant.objects.create(name="Other", slug="other")
        role = Role.objects.create(tenant=other, name="Admin", permissions=["finance.mpesa.configure"])
        Membership.objects.create(tenant=other, user=self.user, role=role)
        with patch.object(TenantMpesaConfiguration, "save", side_effect=RuntimeError("cannot save")):
            with self.assertRaises(RuntimeError):
                configure_mpesa_gateway(user=self.user, tenant=other, environment="SANDBOX",
                    shortcode="600001", consumer_key="key", consumer_secret="secret", passkey="pass")
        self.assertFalse(User.objects.filter(username=f"mpesa-gateway-{other.pk}").exists())
        self.assertFalse(Role.objects.filter(tenant=other, name="M-Pesa Gateway").exists())
        config = configure_mpesa_gateway(user=self.user, tenant=other, environment="SANDBOX",
            shortcode="600001", consumer_key="key", consumer_secret="secret", passkey="pass")
        self.assertIsNotNone(config.pk)

    def test_maximum_length_encrypted_credentials_fit_storage(self):
        value = "a" * 500
        configure_mpesa_gateway(user=self.user, tenant=self.tenant, environment="SANDBOX",
            shortcode="600000", consumer_key=value, consumer_secret=value, passkey=value)
        self.config.refresh_from_db()
        self.assertEqual(self.config.consumer_key, value)
        with connection.cursor() as cursor:
            cursor.execute("SELECT consumer_key FROM finance_tenantmpesaconfiguration WHERE id = %s", [self.config.pk])
            stored = cursor.fetchone()[0]
        self.assertGreater(len(stored), 500)
        self.assertLessEqual(len(stored), 1024)

    def test_status_query_does_not_invent_a_receipt_or_post_money(self):
        request = self._stk_request()
        self.grant_recovery()
        with patch("apps.finance.mpesa_services.MpesaClient") as client:
            client.return_value.query_stk.return_value = {"CheckoutRequestID": request.checkout_request_id, "ResultCode": "0"}
            result = query_stk_request(user=self.user, tenant=self.tenant, request=request)
        self.assertEqual(result.status, "PENDING")
        self.assertEqual(result.provider_query["ResultCode"], "0")
        self.assertEqual(Payment.objects.count(), 0)

    def test_recovery_endpoints_enforce_tenant_and_permissions(self):
        from apps.tenancy.models import Membership, Role, Tenant
        request = self._stk_request()
        callback = self.verified_callback(request)
        client = APIClient()
        client.force_authenticate(self.user)
        other = Tenant.objects.create(name="Other", slug="other")
        role = Role.objects.create(tenant=other, name="Operator", permissions=self.role.permissions)
        Membership.objects.create(tenant=other, user=self.user, role=role)
        for path in (f"mpesa/callbacks/{callback.id}/", f"mpesa/stk-requests/{request.pk}/"):
            self.assertEqual(client.get(f"/api/v1/finance/{path}", HTTP_X_TENANT_SLUG="other").status_code, 404)
        response = client.post(f"/api/v1/finance/mpesa/callbacks/{callback.id}/process/", {}, format="json", HTTP_X_TENANT_SLUG="other")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Payment.objects.count(), 0)
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])
        self.assertEqual(client.get("/api/v1/finance/mpesa/callbacks/", HTTP_X_TENANT_SLUG="school-a").status_code, 403)

    def test_amount_and_idempotency_validation_happens_before_outbound_calls(self):
        client = APIClient()
        client.force_authenticate(self.user)
        base = {"student": str(self.student.pk), "phone_number": "0712345678", "amount": "1000", "idempotency_key": "amount-test"}
        with patch("apps.finance.mpesa_api.initiate_stk_push") as initiate:
            for amount in ("100.99", "0.50", "NaN", "Infinity", "10000000000"):
                response = client.post("/api/v1/finance/mpesa/stk-push/", {**base, "amount": amount}, format="json", HTTP_X_TENANT_SLUG="school-a")
                self.assertEqual(response.status_code, 400)
            response = client.post("/api/v1/finance/mpesa/stk-push/", {key: value for key, value in base.items() if key != "idempotency_key"}, format="json", HTTP_X_TENANT_SLUG="school-a")
            self.assertEqual(response.status_code, 400)
            initiate.assert_not_called()

    def test_identify_lost_response_and_query_endpoints_do_not_resend(self):
        self.grant_recovery()
        self.role.permissions += ["finance.mpesa.stk_push.reconcile"]
        self.role.save(update_fields=["permissions"])
        client = APIClient()
        client.force_authenticate(self.user)
        with patch("apps.finance.mpesa_services.MpesaClient") as gateway:
            gateway.return_value.stk_push.side_effect = MpesaApiError("timeout")
            response = client.post("/api/v1/finance/mpesa/stk-push/", {"student": str(self.student.pk),
                "phone_number": "0712345678", "amount": "1000", "idempotency_key": "lost-http"},
                format="json", HTTP_X_TENANT_SLUG="school-a")
            self.assertEqual(response.status_code, 202)
            request_id = response.data["id"]
            identify = client.post(f"/api/v1/finance/mpesa/stk-requests/{request_id}/identify/",
                {"checkout_request_id": "provider-lookup", "merchant_request_id": "merchant",
                 "evidence": "Independent provider record TEST-LOOKUP"}, format="json", HTTP_X_TENANT_SLUG="school-a")
            self.assertEqual(identify.status_code, 200, identify.data)
            gateway.return_value.query_stk.return_value = {"CheckoutRequestID": "provider-lookup", "ResultCode": "0"}
            query = client.post(f"/api/v1/finance/mpesa/stk-requests/{request_id}/query/", {}, format="json", HTTP_X_TENANT_SLUG="school-a")
            self.assertEqual(query.status_code, 200)
            self.assertEqual(query.data["status"], "PENDING")
            self.assertEqual(gateway.return_value.stk_push.call_count, 1)
        self.assertEqual(Payment.objects.count(), 0)

    def test_rejected_callback_cannot_be_verified_or_posted(self):
        request = self._stk_request()
        callback = self.verified_callback(request)
        client = APIClient()
        client.force_authenticate(self.user)
        base = f"/api/v1/finance/mpesa/callbacks/{callback.pk}"
        response = client.post(base + "/reject/", {"reason": "No matching provider transaction"}, format="json", HTTP_X_TENANT_SLUG="school-a")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.post(base + "/verify/", {"evidence": "other"}, format="json", HTTP_X_TENANT_SLUG="school-a").status_code, 400)
        self.assertEqual(client.post(base + "/process/", {}, format="json", HTTP_X_TENANT_SLUG="school-a").status_code, 400)
        self.assertEqual(Payment.objects.count(), 0)

    def test_config_read_hides_secrets(self):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.get("/api/v1/finance/mpesa-config/", HTTP_X_TENANT_SLUG="school-a")
        self.assertEqual(response.status_code, 200)
        for name in ("consumer_key", "consumer_secret", "passkey"):
            self.assertNotIn(name, response.data)


class MpesaClientValidationTests(SimpleTestCase):
    def gateway_client(self):
        return MpesaClient(SimpleNamespace(environment="SANDBOX", shortcode="600000", passkey="test"))

    def test_fractional_and_nonfinite_amounts_never_make_network_calls(self):
        with patch("apps.finance.mpesa_client.requests.post") as post:
            for value in ("100.99", "0.50", "NaN", "Infinity", "0", "-1"):
                with self.subTest(value=value), self.assertRaises(MpesaApiError):
                    self.gateway_client().stk_push(phone_number="254712345678", amount=Decimal(value),
                        account_reference="ADM", transaction_desc="Fees", callback_url="https://example.invalid")
            post.assert_not_called()

    def test_whole_amount_is_sent_exactly_and_response_is_validated(self):
        client = self.gateway_client()
        with patch.object(client, "_access_token", return_value="token"), patch("apps.finance.mpesa_client.requests.post") as post:
            post.return_value = Mock()
            post.return_value.json.return_value = {"ResponseCode": "0", "MerchantRequestID": "m", "CheckoutRequestID": "c"}
            client.stk_push(phone_number="254712345678", amount=Decimal("100.00"), account_reference="ADM",
                            transaction_desc="Fees", callback_url="https://example.invalid")
            self.assertEqual(post.call_args.kwargs["json"]["Amount"], "100")
            post.return_value.json.return_value = {"ResponseCode": "1"}
            with self.assertRaises(MpesaApiError):
                client.stk_push(phone_number="254712345678", amount=Decimal("100"), account_reference="ADM",
                                transaction_desc="Fees", callback_url="https://example.invalid")

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TransactionTestCase

from apps.tenancy.models import Membership, Role, Tenant, User
from . import test_mpesa as fixtures
from .models import MpesaStkPushRequest, Payment, Receipt, TenantMpesaConfiguration
from .mpesa_services import configure_mpesa_gateway, handle_stk_callback, initiate_stk_push


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transactions")
class GatewayConcurrencyTests(TransactionTestCase):
    setUp = fixtures.MpesaCallbackHandlingTests.setUp
    _stk_request = fixtures.MpesaCallbackHandlingTests._stk_request
    _stk_callback_payload = fixtures.MpesaCallbackHandlingTests._stk_callback_payload

    def run_pair(self, operation, values):
        barrier = Barrier(2)
        def worker(value):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '10s'")
                # Synchronize BEFORE acquiring the contested business lock.
                barrier.wait(timeout=5)
                try:
                    result = operation(value)
                    return ("ok", str(result.pk))
                except ValidationError as error:
                    return ("conflict", str(error))
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, value) for value in values]
            return [future.result(timeout=20) for future in futures]

    def test_competing_success_callbacks_only_one_receipt_posts(self):
        request = self._stk_request()
        def process(receipt):
            return handle_stk_callback(tenant=self.tenant, config=self.config,
                payload=self._stk_callback_payload(request.checkout_request_id, receipt=receipt))
        outcomes = self.run_pair(process, ["RECEIPT-A", "RECEIPT-B"])
        self.assertCountEqual([item[0] for item in outcomes], ["ok", "conflict"])
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)
        request.refresh_from_db()
        self.assertEqual(request.status, "COMPLETED")

    def test_duplicate_success_is_an_idempotent_replay(self):
        request = self._stk_request()
        outcomes = self.run_pair(lambda _: handle_stk_callback(tenant=self.tenant, config=self.config,
            payload=self._stk_callback_payload(request.checkout_request_id)), [1, 2])
        self.assertEqual([item[0] for item in outcomes], ["ok", "ok"])
        self.assertEqual(Payment.objects.count(), 1)

    def test_success_and_failure_cannot_overwrite_each_other(self):
        request = self._stk_request()
        outcomes = self.run_pair(lambda code: handle_stk_callback(tenant=self.tenant, config=self.config,
            payload=self._stk_callback_payload(request.checkout_request_id, result_code=code)), [0, 1032])
        self.assertCountEqual([item[0] for item in outcomes], ["ok", "conflict"])
        request.refresh_from_db()
        self.assertEqual(Payment.objects.count(), 1 if request.status == "COMPLETED" else 0)

    def test_concurrent_initiation_key_sends_once_without_open_transaction(self):
        def send(**kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(MpesaStkPushRequest.objects.count(), 1)
            return {"MerchantRequestID": "merchant", "CheckoutRequestID": "checkout"}
        with patch("apps.finance.mpesa_services.MpesaClient") as client:
            client.return_value.stk_push.side_effect = send
            outcomes = self.run_pair(lambda _: initiate_stk_push(user=self.user, tenant=self.tenant,
                student=self.student, phone_number="0712345678", amount=Decimal("1000"),
                idempotency_key="simultaneous"), [1, 2])
            self.assertEqual([item[0] for item in outcomes], ["ok", "ok"])
            self.assertEqual(outcomes[0][1], outcomes[1][1])
            self.assertEqual(client.return_value.stk_push.call_count, 1)
        self.assertEqual(MpesaStkPushRequest.objects.count(), 1)

    def test_simultaneous_first_configuration_creates_one_identity(self):
        tenant = Tenant.objects.create(name="Fresh", slug="fresh")
        role = Role.objects.create(tenant=tenant, name="Admin", permissions=["finance.mpesa.configure"])
        Membership.objects.create(tenant=tenant, user=self.user, role=role)
        outcomes = self.run_pair(lambda _: configure_mpesa_gateway(user=self.user, tenant=tenant,
            environment="SANDBOX", shortcode="600001", consumer_key="key", consumer_secret="secret", passkey="pass"), [1, 2])
        self.assertEqual([item[0] for item in outcomes], ["ok", "ok"])
        self.assertEqual(TenantMpesaConfiguration.objects.filter(tenant=tenant).count(), 1)
        self.assertEqual(User.objects.filter(username=f"mpesa-gateway-{tenant.pk}").count(), 1)

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.academics.models import AcademicLevel, AcademicYear, Term
from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import FeeCategory, FeeItem, IncomingPayment, MpesaCallbackLog, NumberSeries, Payment, Receipt, TenantMpesaConfiguration
from .services import (
    add_fee_structure_line,
    approve_fee_structure,
    assign_fee_structure,
    create_fee_structure,
    generate_invoice,
    issue_invoice,
)


class MpesaApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.user = User.objects.create_user(username="bursar-api", password="secret")
        self.role = Role.objects.create(
            tenant=self.school_a,
            name="Finance administrator",
            permissions=[
                "finance.mpesa.configure",
                "finance.mpesa.callback.view",
                "finance.mpesa.callback.verify",
                "finance.mpesa.callback.process",
                "finance.mpesa.stk_push.initiate",
                "finance.fee_structure.create",
                "finance.fee_structure.edit",
                "finance.fee_structure.approve",
                "finance.invoice.create",
                "finance.invoice.issue",
                "finance.student_account.view",
            ],
        )
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role)
        self.student = Student.objects.create(tenant=self.school_a, admission_number="ADM-001", first_name="Amina", last_name="Otieno")
        self.client.force_authenticate(self.user)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def _configure(self):
        response = self.client.post(
            "/api/v1/finance/mpesa-config/",
            {"environment": "SANDBOX", "shortcode": "600000", "consumer_key": "key", "consumer_secret": "secret", "passkey": "passkey"},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 201)
        return response.data

    def test_configuration_response_excludes_secrets(self):
        data = self._configure()

        self.assertNotIn("consumer_key", data)
        self.assertNotIn("consumer_secret", data)
        self.assertNotIn("passkey", data)
        self.assertIn("callback_token", data)
        response_text = str(data)
        self.assertNotIn("secret", response_text)

    def test_configure_requires_its_own_permission(self):
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])

        response = self.client.post(
            "/api/v1/finance/mpesa-config/",
            {"environment": "SANDBOX", "shortcode": "600000", "consumer_key": "key", "consumer_secret": "secret", "passkey": "passkey"},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 403)

    def test_rotate_callback_token(self):
        config = self._configure()

        response = self.client.post("/api/v1/finance/mpesa-config/rotate-callback-token/", {}, format="json", **self.headers())

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.data["callback_token"], config["callback_token"])

    @patch("apps.finance.mpesa_api.initiate_stk_push")
    def test_stk_push_initiate_requires_its_own_permission(self, mock_initiate):
        self._configure()
        self.role.permissions = [p for p in self.role.permissions if p != "finance.mpesa.stk_push.initiate"]
        self.role.save(update_fields=["permissions"])

        response = self.client.post(
            "/api/v1/finance/mpesa/stk-push/",
            {"student": str(self.student.id), "phone_number": "0712345678", "idempotency_key": "test-request", "amount": "1000.00"},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 403)
        mock_initiate.assert_not_called()

    def _issued_invoice(self):
        year = AcademicYear.objects.create(tenant=self.school_a, name="2026", starts_on=date(2026, 1, 1), ends_on=date(2026, 12, 31))
        level = AcademicLevel.objects.create(tenant=self.school_a, name="Grade 8", code="G8", sequence=8)
        term = Term.objects.create(tenant=self.school_a, academic_year=year, name="Term 1", starts_on=date(2026, 1, 1), ends_on=date(2026, 4, 30), sequence=1)
        category = FeeCategory.objects.create(tenant=self.school_a, name="Tuition", code="TUITION")
        item = FeeItem.objects.create(tenant=self.school_a, category=category, name="Tuition fee", code="TUITION")
        NumberSeries.objects.create(tenant=self.school_a, document_type="INVOICE", prefix="INV-2026-", padding=6)
        NumberSeries.objects.create(tenant=self.school_a, document_type="RECEIPT", prefix="RCT-2026-", padding=6)
        structure = create_fee_structure(user=self.user, tenant=self.school_a, name="Grade 8 2026", academic_year=year, academic_level=level, term=term)
        add_fee_structure_line(user=self.user, tenant=self.school_a, fee_structure=structure, fee_item=item, amount=Decimal("50000.00"))
        approve_fee_structure(user=self.user, tenant=self.school_a, fee_structure=structure)
        assignment = assign_fee_structure(user=self.user, tenant=self.school_a, student=self.student, fee_structure=structure)
        invoice = generate_invoice(user=self.user, tenant=self.school_a, assignment=assignment)
        issue_invoice(user=self.user, tenant=self.school_a, invoice=invoice)

    @patch("apps.finance.mpesa_services.MpesaClient")
    def test_full_stk_flow_initiate_then_callback_updates_balance(self, mock_client_cls):
        self._issued_invoice()
        config = self._configure()
        mock_client_cls.return_value.stk_push.return_value = {"MerchantRequestID": "merchant-1", "CheckoutRequestID": "checkout-1"}

        initiate_response = self.client.post(
            "/api/v1/finance/mpesa/stk-push/",
            {"student": str(self.student.id), "phone_number": "0712345678", "idempotency_key": "test-request", "amount": "50000.00"},
            format="json", **self.headers(),
        )
        self.assertEqual(initiate_response.status_code, 201)
        self.assertEqual(initiate_response.data["checkout_request_id"], "checkout-1")

        callback_payload = {"Body": {"stkCallback": {
            "ResultCode": 0, "ResultDesc": "ok", "CheckoutRequestID": "checkout-1", "MerchantRequestID": "merchant-1",
            "CallbackMetadata": {"Item": [{"Name": "Amount", "Value": "50000"}, {"Name": "MpesaReceiptNumber", "Value": "NLJ7RT61SV"}]},
        }}}
        callback_response = self.client.post(
            f"/api/v1/finance/mpesa/{config['callback_token']}/stk/callback/", callback_payload, format="json",
        )
        self.assertEqual(callback_response.status_code, 200)

        callback = MpesaCallbackLog.objects.order_by("-created_at").first()
        before = self.client.get(f"/api/v1/finance/students/{self.student.id}/finance/", **self.headers())
        self.assertEqual(before.data["summary"]["outstanding_balance"], Decimal("50000.00"))
        verify = self.client.post(f"/api/v1/finance/mpesa/callbacks/{callback.id}/verify/",
            {"evidence": "Verified against provider statement TEST-001"}, format="json", **self.headers())
        self.assertEqual(verify.status_code, 200)
        process = self.client.post(f"/api/v1/finance/mpesa/callbacks/{callback.id}/process/", {}, format="json", **self.headers())
        self.assertEqual(process.status_code, 200, process.data)
        summary_response = self.client.get(f"/api/v1/finance/students/{self.student.id}/finance/", **self.headers())
        self.assertEqual(summary_response.data["summary"]["outstanding_balance"], Decimal("0.00"))

    def test_c2b_validation_and_confirmation_flow(self):
        self._issued_invoice()
        config = self._configure()

        validation_response = self.client.post(
            f"/api/v1/finance/mpesa/{config['callback_token']}/c2b/validation/",
            {"TransID": "QGH001", "TransAmount": "50000", "BillRefNumber": self.student.admission_number},
            format="json",
        )
        self.assertEqual(validation_response.status_code, 200)
        self.assertEqual(validation_response.data["ResultCode"], 0)

        confirmation_response = self.client.post(
            f"/api/v1/finance/mpesa/{config['callback_token']}/c2b/confirmation/",
            {"TransID": "QGH001", "TransAmount": "50000", "BillRefNumber": self.student.admission_number},
            format="json",
        )
        self.assertEqual(confirmation_response.status_code, 200)

        callback = MpesaCallbackLog.objects.order_by("-created_at").first()
        before = self.client.get(f"/api/v1/finance/students/{self.student.id}/finance/", **self.headers())
        self.assertEqual(before.data["summary"]["outstanding_balance"], Decimal("50000.00"))
        verify = self.client.post(f"/api/v1/finance/mpesa/callbacks/{callback.id}/verify/",
            {"evidence": "Verified against provider statement TEST-001"}, format="json", **self.headers())
        self.assertEqual(verify.status_code, 200)
        process = self.client.post(f"/api/v1/finance/mpesa/callbacks/{callback.id}/process/", {}, format="json", **self.headers())
        self.assertEqual(process.status_code, 200, process.data)
        summary_response = self.client.get(f"/api/v1/finance/students/{self.student.id}/finance/", **self.headers())
        self.assertEqual(summary_response.data["summary"]["outstanding_balance"], Decimal("0.00"))

    def test_unknown_callback_token_is_a_404_on_every_webhook(self):
        bogus_token = "does-not-exist"
        for path in ("c2b/validation", "c2b/confirmation", "stk/callback"):
            response = self.client.post(f"/api/v1/finance/mpesa/{bogus_token}/{path}/", {}, format="json")
            self.assertEqual(response.status_code, 404, path)

    def test_callback_acknowledgement_requires_durable_storage(self):
        config = self._configure()
        for path in ("c2b/confirmation", "stk/callback"):
            with self.subTest(path=path), patch("apps.finance.mpesa_api.log_mpesa_callback", side_effect=RuntimeError("storage down")):
                with self.assertRaises(RuntimeError):
                    self.client.post(f"/api/v1/finance/mpesa/{config['callback_token']}/{path}/", {}, format="json")

    def test_malformed_callbacks_are_retained_without_processing(self):
        config = self._configure()
        for path in ("c2b/confirmation", "stk/callback"):
            response = self.client.post(f"/api/v1/finance/mpesa/{config['callback_token']}/{path}/", [], format="json")
            self.assertEqual(response.status_code, 200)
        self.assertEqual(MpesaCallbackLog.objects.filter(status="RECEIVED").count(), 2)

    def test_duplicate_webhook_deliveries_never_create_financial_records_on_their_own(self):
        """RC Area 3: the public AllowAny webhook must only ever create
        durable MpesaCallbackLog evidence, never IncomingPayment/Payment/
        Receipt directly -- that's the whole point of the verify->process
        boundary (mpesa_services.process_mpesa_callback). Concurrency isn't
        actually load-bearing here: MpesaC2BConfirmationView/
        MpesaStkCallbackView unconditionally log-then-ACK with no
        check-then-act logic in between, so two duplicate deliveries prove
        the same guarantee two truly concurrent ones would -- there's no
        code path in this view that could behave differently under overlap.
        Duplicate-delivery replay safety for the *authenticated* verify/
        process pipeline itself is already covered by
        test_mpesa_concurrency.py and test_mpesa_recovery.py.
        """
        self._issued_invoice()
        config = self._configure()
        payload = {"TransID": "QGH500", "TransAmount": "50000", "BillRefNumber": self.student.admission_number}

        for _ in range(2):
            response = self.client.post(
                f"/api/v1/finance/mpesa/{config['callback_token']}/c2b/confirmation/", payload, format="json",
            )
            self.assertEqual(response.status_code, 200)

        self.assertEqual(MpesaCallbackLog.objects.filter(status="RECEIVED").count(), 2)
        self.assertEqual(IncomingPayment.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(Receipt.objects.count(), 0)

    def test_webhook_views_are_throttled_on_their_own_scope(self):
        from apps.finance.mpesa_api import CallbackTokenThrottle, MpesaC2BConfirmationView, MpesaC2BValidationView, MpesaStkCallbackView
        from rest_framework.throttling import ScopedRateThrottle

        for view_class in (MpesaC2BValidationView, MpesaC2BConfirmationView, MpesaStkCallbackView):
            self.assertEqual(view_class.throttle_classes, [CallbackTokenThrottle])
            self.assertTrue(issubclass(CallbackTokenThrottle, ScopedRateThrottle))
            self.assertEqual(view_class.throttle_scope, "mpesa_callback")

    def test_a_throttled_webhook_request_leaves_no_partial_state(self):
        """A 429 must happen before log_mpesa_callback runs at all -- proving
        throttling can't leave a half-written callback record, and (since
        Milestone 22.1 deliberately keeps this rate generous) that retrying
        after a 429 is always safe because nothing was recorded the first time.

        DRF throttle classes snapshot DEFAULT_THROTTLE_RATES into a class
        attribute at import time -- override_settings(REST_FRAMEWORK=...)
        doesn't reach already-imported throttle classes, so the rate is
        patched directly on ScopedRateThrottle instead.
        """
        from unittest.mock import patch

        from django.core.cache import cache
        from rest_framework.throttling import ScopedRateThrottle

        config = self._configure()
        cache.clear()
        self.addCleanup(cache.clear)

        with patch.object(ScopedRateThrottle, "THROTTLE_RATES", {"mpesa_callback": "1/min"}):
            first = self.client.post(
                f"/api/v1/finance/mpesa/{config['callback_token']}/c2b/validation/",
                {"TransID": "QGH900", "TransAmount": "1000", "BillRefNumber": self.student.admission_number},
                format="json",
            )
            self.assertEqual(first.status_code, 200)
            second = self.client.post(
                f"/api/v1/finance/mpesa/{config['callback_token']}/c2b/validation/",
                {"TransID": "QGH901", "TransAmount": "1000", "BillRefNumber": self.student.admission_number},
                format="json",
            )
            self.assertEqual(second.status_code, 429)

        self.assertEqual(MpesaCallbackLog.objects.filter(status="RECEIVED").count(), 1)

    def test_callback_throttle_isolates_tenants_from_each_others_budget(self):
        """RC Area 8 (backend RC synthesis): the public webhook throttle must
        isolate tenants from each other's capacity, the same way every other
        operational allowance in this codebase is scoped per-tenant. DRF's
        stock ScopedRateThrottle keys anonymous requests by source IP
        (SimpleRateThrottle.get_ident), not by the callback token that
        already resolves the request to exactly one tenant -- so today, one
        tenant's own webhook traffic can exhaust *every* tenant's shared
        bucket if their callbacks happen to arrive from the same source IP
        (plausible in production: Safaricom's Daraja callbacks and any
        shared NAT/load-balancer path on our side). This test proves that
        directly: exhaust School A's budget, then confirm School B's very
        next webhook call -- a different tenant, a different callback token,
        the same test client/source IP -- is unaffected.
        """
        from django.core.cache import cache
        from rest_framework.throttling import ScopedRateThrottle

        config_a = self._configure()
        cache.clear()
        self.addCleanup(cache.clear)

        school_b = Tenant.objects.create(name="School B", slug="school-b")
        user_b = User.objects.create_user(username="bursar-b", password="secret")
        role_b = Role.objects.create(tenant=school_b, name="Finance administrator", permissions=["finance.mpesa.configure"])
        Membership.objects.create(tenant=school_b, user=user_b, role=role_b)
        client_b = APIClient()
        client_b.force_authenticate(user_b)
        response = client_b.post(
            "/api/v1/finance/mpesa-config/",
            {"environment": "SANDBOX", "shortcode": "600001", "consumer_key": "key", "consumer_secret": "secret", "passkey": "passkey"},
            format="json", HTTP_X_TENANT_SLUG="school-b",
        )
        self.assertEqual(response.status_code, 201)
        config_b = response.data

        with patch.object(ScopedRateThrottle, "THROTTLE_RATES", {"mpesa_callback": "1/min"}):
            first_a = self.client.post(
                f"/api/v1/finance/mpesa/{config_a['callback_token']}/c2b/validation/",
                {"TransID": "ISO-A-1", "TransAmount": "1000", "BillRefNumber": self.student.admission_number},
                format="json",
            )
            self.assertEqual(first_a.status_code, 200)
            exhausted_a = self.client.post(
                f"/api/v1/finance/mpesa/{config_a['callback_token']}/c2b/validation/",
                {"TransID": "ISO-A-2", "TransAmount": "1000", "BillRefNumber": self.student.admission_number},
                format="json",
            )
            self.assertEqual(exhausted_a.status_code, 429)

            first_b = self.client.post(
                f"/api/v1/finance/mpesa/{config_b['callback_token']}/c2b/validation/",
                {"TransID": "ISO-B-1", "TransAmount": "1000", "BillRefNumber": "irrelevant"},
                format="json",
            )
            self.assertEqual(
                first_b.status_code, 200,
                "School B's webhook was throttled by School A's own traffic -- "
                "the mpesa_callback throttle bucket is shared by source IP, "
                "not isolated per tenant.",
            )

    def test_stk_push_malformed_student_id_is_a_400_not_a_500(self):
        self._configure()

        response = self.client.post(
            "/api/v1/finance/mpesa/stk-push/",
            {"student": "not-a-uuid", "phone_number": "0712345678", "idempotency_key": "test-request", "amount": "1000.00"},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)

    def test_stk_push_nonexistent_student_is_a_404(self):
        self._configure()

        response = self.client.post(
            "/api/v1/finance/mpesa/stk-push/",
            {"student": str(uuid.uuid4()), "phone_number": "0712345678", "idempotency_key": "test-request", "amount": "1000.00"},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 404)

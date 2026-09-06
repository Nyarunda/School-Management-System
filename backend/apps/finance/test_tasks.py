from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import IncomingPayment, NumberSeries, PaymentMethod, ReconciliationStatus
from .mpesa_services import configure_mpesa_gateway
from .services import ingest_incoming_payment
from .tasks import resweep_unmatched_incoming_payments


class ResweepUnmatchedIncomingPaymentsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.user = User.objects.create_user(username="bursar", password="secret")
        self.role = Role.objects.create(
            tenant=self.tenant, name="Bursar",
            permissions=["finance.reconciliation.ingest", "finance.mpesa.configure"],
        )
        Membership.objects.create(tenant=self.tenant, user=self.user, role=self.role)
        self.payment_method = PaymentMethod.objects.create(tenant=self.tenant, name="Bank", code="BANK")
        # record_payment (invoked by the auto-match this task triggers) needs
        # a receipt number series, same as apps/finance/test_mpesa.py's fixture.
        NumberSeries.objects.create(tenant=self.tenant, document_type="RECEIPT", prefix="RCT-", padding=6)

    def _ingest_stale(self, reference, transaction_id):
        incoming = ingest_incoming_payment(
            user=self.user, tenant=self.tenant, payment_method=self.payment_method,
            amount=Decimal("1000.00"), external_reference=reference, external_transaction_id=transaction_id,
        )
        IncomingPayment.objects.filter(pk=incoming.pk).update(created_at=timezone.now() - timedelta(hours=2))
        incoming.refresh_from_db()
        return incoming

    def test_resweep_is_a_no_op_without_an_active_mpesa_configuration(self):
        incoming = self._ingest_stale("no student yet", "TXN-1")

        resweep_unmatched_incoming_payments()

        incoming.refresh_from_db()
        self.assertEqual(incoming.status, ReconciliationStatus.UNMATCHED)

    def test_resweep_matches_once_a_new_student_makes_the_reference_unambiguous(self):
        configure_mpesa_gateway(
            user=self.user, tenant=self.tenant, environment="SANDBOX", shortcode="600000",
            consumer_key="key-1", consumer_secret="secret-1", passkey="passkey-1",
        )
        incoming = self._ingest_stale("payment for ADM-0099", "TXN-2")

        # The student didn't exist at ingestion time, so recognition found no
        # match and it stayed UNMATCHED -- unrelated to M-Pesa trust, this is
        # just "the reference became recognizable later."
        Student.objects.create(tenant=self.tenant, admission_number="ADM-0099", first_name="Jane", last_name="Doe")

        resweep_unmatched_incoming_payments()

        incoming.refresh_from_db()
        self.assertEqual(incoming.status, ReconciliationStatus.MATCHED)

    def test_resweep_ignores_entries_not_yet_past_the_staleness_window(self):
        configure_mpesa_gateway(
            user=self.user, tenant=self.tenant, environment="SANDBOX", shortcode="600000",
            consumer_key="key-1", consumer_secret="secret-1", passkey="passkey-1",
        )
        incoming = ingest_incoming_payment(
            user=self.user, tenant=self.tenant, payment_method=self.payment_method,
            amount=Decimal("1000.00"), external_reference="payment for ADM-0100", external_transaction_id="TXN-3",
        )
        Student.objects.create(tenant=self.tenant, admission_number="ADM-0100", first_name="John", last_name="Doe")

        resweep_unmatched_incoming_payments()

        incoming.refresh_from_db()
        self.assertEqual(incoming.status, ReconciliationStatus.UNMATCHED)

    def test_resweep_does_not_touch_already_matched_or_ignored_entries(self):
        configure_mpesa_gateway(
            user=self.user, tenant=self.tenant, environment="SANDBOX", shortcode="600000",
            consumer_key="key-1", consumer_secret="secret-1", passkey="passkey-1",
        )
        matched_reference_incoming = self._ingest_stale("payment for ADM-0101", "TXN-4")
        Student.objects.create(tenant=self.tenant, admission_number="ADM-0101", first_name="Amy", last_name="Doe")
        resweep_unmatched_incoming_payments()
        matched_reference_incoming.refresh_from_db()
        self.assertEqual(matched_reference_incoming.status, ReconciliationStatus.MATCHED)
        matched_payment_id = matched_reference_incoming.matched_payment_id

        # A second sweep must not re-touch an already-resolved entry.
        resweep_unmatched_incoming_payments()
        matched_reference_incoming.refresh_from_db()
        self.assertEqual(matched_reference_incoming.matched_payment_id, matched_payment_id)

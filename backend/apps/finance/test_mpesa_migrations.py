from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class GatewayMigrationTests(TransactionTestCase):
    def test_existing_ciphertext_and_pending_checkout_survive_expansion(self):
        before = [("finance", "0007_mpesacallbacklog_mpesastkpushrequest_and_more")]
        after = [("finance", "0008_mpesa_recovery_and_verification")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            tenant = apps.get_model("tenancy", "Tenant").objects.create(name="Legacy", slug="legacy")
            user = apps.get_model("tenancy", "User").objects.create(username="legacy", password="!")
            method = apps.get_model("finance", "PaymentMethod").objects.create(tenant=tenant, name="M-Pesa", code="MPESA")
            config = apps.get_model("finance", "TenantMpesaConfiguration").objects.create(
                tenant=tenant, payment_method=method, system_user=user, callback_token="legacy-token",
                shortcode="600000", consumer_key="old-key", consumer_secret="old-secret", passkey="old-pass",
            )
            student = apps.get_model("students", "Student").objects.create(tenant=tenant,
                admission_number="LEGACY", first_name="Existing", last_name="Student")
            request = apps.get_model("finance", "MpesaStkPushRequest").objects.create(
                tenant=tenant, student=student, phone_number="254712345678", amount="1000.00",
                account_reference="LEGACY", merchant_request_id="old-merchant", checkout_request_id="old-checkout",
            )
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            apps = executor.loader.project_state(after).apps
            self.assertEqual(apps.get_model("finance", "TenantMpesaConfiguration").objects.get(pk=config.pk).consumer_secret, "old-secret")
            migrated = apps.get_model("finance", "MpesaStkPushRequest").objects.get(pk=request.pk)
            self.assertEqual(migrated.checkout_request_id, "old-checkout")
            self.assertEqual(migrated.status, "PENDING")
            self.assertIsNone(migrated.idempotency_key)
            self.assertEqual(migrated.provider_query, {})
        finally:
            MigrationExecutor(connection).migrate(after)

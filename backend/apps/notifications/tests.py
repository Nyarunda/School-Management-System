from datetime import date, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.activity.durable_work import DurableWorkStatus
from apps.guardians.models import Guardian, StudentGuardian
from apps.staff.models import Employee, EmploymentType
from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from .catalogue import _resolve_employee_recipients, _resolve_guardian_recipients
from .gateways.base import NotificationGateway
from .models import (
    CommunicationChannel,
    CommunicationSetup,
    NotificationChannel,
    NotificationDeliveryAttempt,
    NotificationOutbox,
    NotificationProviderConfig,
    NotificationRecipientType,
    NotificationRule,
    NotificationTemplate,
    UserNotification,
)
from .services import (
    configure_communication_setup,
    configure_provider,
    create_notification_rule,
    create_notification_template,
    enqueue_notification,
    mark_user_notification_read,
    publish_notification_event,
    retry_notification,
    set_channel_enabled,
    update_notification_rule,
    update_notification_template,
)
from .tasks import dispatch_pending_notifications, reap_stale_notifications


class EnqueueNotificationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")

    def test_enqueue_replay_with_a_different_payload_is_rejected(self):
        enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0711111111",
            message_type="invoice_issued", idempotency_key="invoice-issued:1:1",
        )
        with self.assertRaises(ValidationError):
            enqueue_notification(
                tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0722222222",
                message_type="invoice_issued", idempotency_key="invoice-issued:1:1",
            )
        self.assertEqual(NotificationOutbox.objects.count(), 1)

    def test_enqueue_is_idempotent_on_the_same_key(self):
        first = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:1:1",
        )
        second = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:1:1",
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(NotificationOutbox.objects.count(), 1)

    def test_enqueue_creates_distinct_rows_for_distinct_keys(self):
        enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:1:1",
        )
        enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:2:1",
        )
        self.assertEqual(NotificationOutbox.objects.count(), 2)


class DispatchPendingNotificationsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")

    def test_dispatch_marks_a_pending_row_processed_and_records_an_attempt(self):
        outbox = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:1:1", context={"subject": "", "body": "Hi"},
        )
        dispatch_pending_notifications()
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, DurableWorkStatus.PROCESSED)
        self.assertIsNotNone(outbox.processed_at)
        attempts = list(NotificationDeliveryAttempt.objects.filter(notification=outbox))
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].status, "SENT")
        self.assertTrue(attempts[0].provider_reference)

    def test_forced_failure_retries_then_dead_letters(self):
        outbox = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:1:1", context={"subject": "", "body": "Hi"},
        )

        class BoomGateway(NotificationGateway):
            def send(self, **kwargs):
                raise RuntimeError("provider unreachable")

        with patch("apps.notifications.tasks.resolve_gateway", return_value=BoomGateway()):
            for _ in range(5):
                outbox.refresh_from_db()
                outbox.available_at = timezone.now() - timedelta(seconds=1)
                outbox.save(update_fields=["available_at"])
                dispatch_pending_notifications()

        outbox.refresh_from_db()
        self.assertEqual(outbox.status, DurableWorkStatus.FAILED)
        self.assertIn("provider unreachable", outbox.last_error)
        self.assertEqual(NotificationDeliveryAttempt.objects.filter(notification=outbox, status="FAILED").count(), 5)

    def test_in_app_dispatch_creates_a_user_notification(self):
        user = User.objects.create_user(username="staffer", password="secret")
        outbox = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.IN_APP, recipient=str(user.id),
            message_type="leave_approved", idempotency_key="leave-approved:1",
            context={"subject": "Leave approved", "body": "Your leave was approved"},
        )
        dispatch_pending_notifications()
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, DurableWorkStatus.PROCESSED)
        notification = UserNotification.objects.get(tenant=self.tenant, user=user)
        self.assertEqual(notification.title, "Leave approved")
        self.assertEqual(notification.message, "Your leave was approved")


class ReapStaleNotificationsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")

    def test_reap_stale_reclaims_an_expired_processing_row(self):
        outbox = NotificationOutbox.objects.create(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="invoice_issued", idempotency_key="invoice-issued:1:1",
            status=DurableWorkStatus.PROCESSING, lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        reap_stale_notifications()
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, DurableWorkStatus.PENDING)


class NotificationFoundationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")
        self.admin = User.objects.create_user(username="admin", password="secret")
        self.admin_role = Role.objects.create(
            tenant=self.tenant, name="Comms Admin",
            permissions=[
                "notifications.setup.view", "notifications.setup.manage", "notifications.templates.view",
                "notifications.templates.manage", "notifications.rules.view", "notifications.rules.manage",
                "notifications.record.view", "notifications.retry",
            ],
        )
        Membership.objects.create(tenant=self.tenant, user=self.admin, role=self.admin_role)

    def _make_template(self, *, channel=NotificationChannel.SMS, code="TEST_TEMPLATE", body="Hello {{ guardian_name }}", subject=""):
        return create_notification_template(user=self.admin, tenant=self.tenant, code=code, name=code, channel=channel, body=body, subject=subject)

    def _make_rule(self, *, event_code, recipient_type, channel=NotificationChannel.SMS, template=None, enabled=True):
        template = template or self._make_template(channel=channel, code=f"{event_code}-{recipient_type}-{channel}")
        return create_notification_rule(
            user=self.admin, tenant=self.tenant, event_code=event_code, recipient_type=recipient_type,
            channel=channel, template=template, enabled=enabled,
        )

    def _enable_channel(self, channel):
        set_channel_enabled(user=self.admin, tenant=self.tenant, channel=channel, enabled=True)


class CommunicationSetupTests(NotificationFoundationTests):
    def test_configure_creates_and_updates(self):
        setup = configure_communication_setup(user=self.admin, tenant=self.tenant, notifications_enabled=False)
        self.assertFalse(setup.notifications_enabled)
        updated = configure_communication_setup(user=self.admin, tenant=self.tenant, default_country_code="254")
        self.assertEqual(updated.default_country_code, "254")
        self.assertFalse(updated.notifications_enabled)  # untouched fields persist
        self.assertEqual(CommunicationSetup.objects.filter(tenant=self.tenant).count(), 1)

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValidationError):
            configure_communication_setup(user=self.admin, tenant=self.tenant, bogus_field=True)


class CommunicationChannelTests(NotificationFoundationTests):
    def test_channel_defaults_to_disabled_and_can_be_toggled(self):
        self.assertFalse(CommunicationChannel.objects.filter(tenant=self.tenant, channel=NotificationChannel.SMS).exists())
        row = set_channel_enabled(user=self.admin, tenant=self.tenant, channel=NotificationChannel.SMS, enabled=True)
        self.assertTrue(row.enabled)
        row = set_channel_enabled(user=self.admin, tenant=self.tenant, channel=NotificationChannel.SMS, enabled=False)
        self.assertFalse(row.enabled)


class NotificationProviderConfigTests(NotificationFoundationTests):
    def test_configure_encrypts_credentials_and_rotation_preserves_untouched_fields(self):
        config = configure_provider(
            user=self.admin, tenant=self.tenant, channel=NotificationChannel.SMS, provider="STUB",
            sender_id="SCHOOL", api_key="key-1", api_secret="secret-1",
        )
        config.refresh_from_db()
        self.assertEqual(config.encrypted_api_key, "key-1")  # decrypts transparently on read
        with connection.cursor() as cursor:
            cursor.execute("SELECT encrypted_api_key FROM notifications_notificationproviderconfig WHERE id = %s", [str(config.id)])
            stored_value = cursor.fetchone()[0]
        self.assertNotEqual(stored_value, "key-1")  # stored ciphertext, not plaintext
        rotated = configure_provider(
            user=self.admin, tenant=self.tenant, channel=NotificationChannel.SMS, provider="STUB", sender_id="SCHOOL2",
        )
        self.assertEqual(rotated.id, config.id)
        self.assertEqual(rotated.sender_id, "SCHOOL2")
        self.assertEqual(rotated.encrypted_api_key, "key-1")  # untouched when api_key omitted


class NotificationTemplateTests(NotificationFoundationTests):
    def test_duplicate_code_is_rejected(self):
        self._make_template(code="DUP")
        with self.assertRaises(ValidationError):
            self._make_template(code="dup")  # normalized -- collides case-insensitively

    def test_update_rejects_unknown_field(self):
        template = self._make_template()
        with self.assertRaises(ValidationError):
            update_notification_template(user=self.admin, tenant=self.tenant, template=template, channel=NotificationChannel.EMAIL)


class NotificationRuleTests(NotificationFoundationTests):
    def test_unknown_event_code_is_rejected(self):
        template = self._make_template()
        with self.assertRaises(ValidationError):
            create_notification_rule(
                user=self.admin, tenant=self.tenant, event_code="not.a.real.event",
                recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS, template=template,
            )

    def test_template_channel_mismatch_is_rejected(self):
        template = self._make_template(channel=NotificationChannel.EMAIL)
        with self.assertRaises(ValidationError):
            create_notification_rule(
                user=self.admin, tenant=self.tenant, event_code="finance.payment.received",
                recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS, template=template,
            )

    def test_guardian_in_app_combination_is_rejected(self):
        template = self._make_template(channel=NotificationChannel.IN_APP)
        with self.assertRaises(ValidationError):
            create_notification_rule(
                user=self.admin, tenant=self.tenant, event_code="finance.payment.received",
                recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.IN_APP, template=template,
            )

    def test_duplicate_rule_for_same_event_recipient_channel_is_rejected(self):
        self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN)
        with self.assertRaises(ValidationError):
            self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN)

    def test_update_rule_revalidates_template_channel(self):
        rule = self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS)
        email_template = self._make_template(channel=NotificationChannel.EMAIL, code="EMAIL_TPL")
        with self.assertRaises(ValidationError):
            update_notification_rule(user=self.admin, tenant=self.tenant, rule=rule, template=email_template)


class RecipientResolverTests(NotificationFoundationTests):
    def setUp(self):
        super().setUp()
        self.student = Student.objects.create(tenant=self.tenant, admission_number="S-001", first_name="Kim", last_name="Otieno")
        self.primary_guardian = Guardian.objects.create(tenant=self.tenant, first_name="Ann", last_name="Otieno", phone_number="0700000001", email="ann@example.com")
        self.non_primary_guardian = Guardian.objects.create(tenant=self.tenant, first_name="Ben", last_name="Otieno", phone_number="0700000002", email="ben@example.com")
        StudentGuardian.objects.create(tenant=self.tenant, student=self.student, guardian=self.primary_guardian, relationship="Mother", is_primary=True)
        StudentGuardian.objects.create(tenant=self.tenant, student=self.student, guardian=self.non_primary_guardian, relationship="Uncle", is_primary=False, is_emergency_contact=False)

    def test_guardian_resolver_only_returns_primary_or_emergency_contacts(self):
        results = _resolve_guardian_recipients(tenant=self.tenant, channel=NotificationChannel.SMS, recipient_refs={"student": self.student})
        self.assertEqual([contact for contact, _ in results], ["0700000001"])

    def test_guardian_resolver_skips_blank_contact(self):
        self.primary_guardian.email = ""
        self.primary_guardian.save(update_fields=["email"])
        results = _resolve_guardian_recipients(tenant=self.tenant, channel=NotificationChannel.EMAIL, recipient_refs={"student": self.student})
        self.assertEqual(results, [])

    def test_employee_resolver_skips_in_app_without_linked_account(self):
        employee = Employee.objects.create(
            tenant=self.tenant, employee_number="E-001", first_name="Joy", last_name="Wanjiru", job_title="Teacher",
            employment_type=EmploymentType.PERMANENT, hire_date=date(2020, 1, 1), phone_number="0711111111", email="joy@example.com",
        )
        results = _resolve_employee_recipients(tenant=self.tenant, channel=NotificationChannel.IN_APP, recipient_refs={"employee": employee})
        self.assertEqual(results, [])
        results = _resolve_employee_recipients(tenant=self.tenant, channel=NotificationChannel.SMS, recipient_refs={"employee": employee})
        self.assertEqual([contact for contact, _ in results], ["0711111111"])


class PublishNotificationEventTests(NotificationFoundationTests):
    def setUp(self):
        super().setUp()
        self.student = Student.objects.create(tenant=self.tenant, admission_number="S-100", first_name="Lee", last_name="Achieng")
        self.guardian = Guardian.objects.create(tenant=self.tenant, first_name="Rose", last_name="Achieng", phone_number="0700000009", email="rose@example.com")
        StudentGuardian.objects.create(tenant=self.tenant, student=self.student, guardian=self.guardian, relationship="Mother", is_primary=True)

    def test_no_rows_when_tenant_notifications_are_disabled(self):
        configure_communication_setup(user=self.admin, tenant=self.tenant, notifications_enabled=False)
        self._enable_channel(NotificationChannel.SMS)
        self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS)
        created = publish_notification_event(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:1",
            context={}, recipient_refs={"student": self.student},
        )
        self.assertEqual(created, [])
        self.assertEqual(NotificationOutbox.objects.count(), 0)

    def test_disabled_channel_is_skipped_others_still_created(self):
        self._enable_channel(NotificationChannel.EMAIL)  # SMS left disabled
        self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS)
        self._make_rule(
            event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.EMAIL,
            template=self._make_template(channel=NotificationChannel.EMAIL, code="EMAIL_RULE", body="Hi {{ guardian_name }}"),
        )
        created = publish_notification_event(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:1",
            context={}, recipient_refs={"student": self.student},
        )
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].channel, NotificationChannel.EMAIL)

    def test_republishing_the_same_dedupe_key_is_idempotent(self):
        self._enable_channel(NotificationChannel.SMS)
        self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS)
        publish_notification_event(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:1",
            context={}, recipient_refs={"student": self.student},
        )
        publish_notification_event(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:1",
            context={}, recipient_refs={"student": self.student},
        )
        self.assertEqual(NotificationOutbox.objects.count(), 1)

    def test_rendered_content_merges_resolver_and_caller_context(self):
        self._enable_channel(NotificationChannel.SMS)
        self._make_rule(
            event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS,
            template=self._make_template(code="RENDER_TPL", body="Dear {{ guardian_name }}, payment of {{ amount }} received"),
        )
        created = publish_notification_event(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:2",
            context={"amount": "500"}, recipient_refs={"student": self.student},
        )
        self.assertEqual(created[0].context["body"], "Dear Rose Achieng, payment of 500 received")

    def test_missing_context_variable_fails_clearly(self):
        self._enable_channel(NotificationChannel.SMS)
        self._make_rule(
            event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS,
            template=self._make_template(code="BAD_TPL", body="Amount: {{ amount }}"),
        )
        with self.assertRaises(ValidationError):
            publish_notification_event(
                tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:3",
                context={}, recipient_refs={"student": self.student},
            )

    def test_unknown_event_code_raises(self):
        with self.assertRaises(ValueError):
            publish_notification_event(
                tenant=self.tenant, event_code="not.a.real.event", dedupe_key="x:1", context={}, recipient_refs={},
            )

    def test_unconfigured_tenant_still_notifies(self):
        # No CommunicationSetup row at all -- absence means enabled=True.
        self._enable_channel(NotificationChannel.SMS)
        self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS)
        created = publish_notification_event(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:4",
            context={}, recipient_refs={"student": self.student},
        )
        self.assertEqual(len(created), 1)


class RetryNotificationTests(NotificationFoundationTests):
    def test_retry_resets_a_failed_row(self):
        outbox = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0700000000",
            message_type="x", idempotency_key="x:1", context={"subject": "", "body": "hi"},
        )
        outbox.status = DurableWorkStatus.FAILED
        outbox.last_error = "boom"
        outbox.save(update_fields=["status", "last_error"])
        retried = retry_notification(user=self.admin, tenant=self.tenant, outbox=outbox)
        self.assertEqual(retried.status, DurableWorkStatus.PENDING)
        self.assertEqual(retried.last_error, "")

    def test_retry_rejects_a_non_failed_row(self):
        outbox = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0700000000",
            message_type="x", idempotency_key="x:2", context={"subject": "", "body": "hi"},
        )
        with self.assertRaises(ValidationError):
            retry_notification(user=self.admin, tenant=self.tenant, outbox=outbox)


class UserNotificationTests(NotificationFoundationTests):
    def test_mark_read_is_idempotent_and_owner_only(self):
        other_user = User.objects.create_user(username="other", password="secret")
        notification = UserNotification.objects.create(tenant=self.tenant, user=self.admin, title="Hi", message="Body")
        with self.assertRaises(ValidationError):
            mark_user_notification_read(user=other_user, notification=notification)
        updated = mark_user_notification_read(user=self.admin, notification=notification)
        self.assertIsNotNone(updated.read_at)
        first_read_at = updated.read_at
        updated_again = mark_user_notification_read(user=self.admin, notification=notification)
        self.assertEqual(updated_again.read_at, first_read_at)

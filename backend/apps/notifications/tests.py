from datetime import date, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.activity.durable_work import DurableWorkStatus
from apps.activity.models import ActivityEvent
from apps.guardians.models import Guardian, StudentGuardian
from apps.staff.models import Employee, EmploymentType
from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from .catalogue import _resolve_employee_recipients, _resolve_guardian_recipients
from .gateways.base import NotificationGateway
from .gateways.in_app import InAppGateway
from .gateways import resolve_gateway
from .models import (
    CommunicationChannel,
    CommunicationSetup,
    GuardianRecipientPolicy,
    NotificationChannel,
    NotificationDeliveryAttempt,
    NotificationEvent,
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
    expand_notification_event,
    mark_user_notification_read,
    publish_notification_event,
    retry_notification,
    set_channel_enabled,
    update_notification_rule,
    update_notification_template,
)
from .tasks import dispatch_pending_notifications, expand_pending_notification_events, reap_stale_notifications


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

    def test_in_app_and_external_recipients_are_mutually_exclusive(self):
        user = User.objects.create_user(username="staffer", password="secret")
        with self.assertRaises(Exception):
            # Both recipient and recipient_user set -- violates the CheckConstraint.
            NotificationOutbox.objects.create(
                tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0700000000", recipient_user=user,
                message_type="x", idempotency_key="bad:1",
            )


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
            tenant=self.tenant, channel=NotificationChannel.IN_APP, recipient_user_id=user.id,
            message_type="leave_approved", idempotency_key="leave-approved:1",
            context={"subject": "Leave approved", "body": "Your leave was approved"},
        )
        dispatch_pending_notifications()
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, DurableWorkStatus.PROCESSED)
        notification = UserNotification.objects.get(tenant=self.tenant, user=user)
        self.assertEqual(notification.title, "Leave approved")
        self.assertEqual(notification.message, "Your leave was approved")
        self.assertEqual(notification.source_notification_id, outbox.id)

    def test_unsupported_provider_fails_the_attempt_instead_of_sending(self):
        NotificationProviderConfig.objects.create(tenant=self.tenant, channel=NotificationChannel.SMS, provider="TWILIO", is_active=True)
        outbox = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0712345678",
            message_type="x", idempotency_key="x:1", context={"subject": "", "body": "Hi"},
        )
        dispatch_pending_notifications()
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, DurableWorkStatus.PENDING)  # first failure just backs off, doesn't dead-letter yet
        self.assertIn("Unsupported notification provider", outbox.last_error)


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


class InAppGatewayIdempotencyTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")
        self.user = User.objects.create_user(username="staffer", password="secret")
        self.outbox = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.IN_APP, recipient_user_id=self.user.id,
            message_type="x", idempotency_key="x:1", context={"subject": "Hi", "body": "Body"},
        )

    def test_retrying_after_a_crash_does_not_duplicate_the_in_app_notification(self):
        gateway = InAppGateway()
        gateway.send(outbox=self.outbox)  # first "attempt" -- creates the row
        gateway.send(outbox=self.outbox)  # simulates a retry after a crash before mark_processed()
        self.assertEqual(UserNotification.objects.filter(source_notification=self.outbox).count(), 1)


class ResolveGatewayTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")

    def test_unconfigured_channel_resolves_to_the_stub(self):
        gateway = resolve_gateway(tenant=self.tenant, channel=NotificationChannel.SMS)
        self.assertEqual(type(gateway).__name__, "StubSMSGateway")

    def test_explicit_stub_provider_resolves_to_the_stub(self):
        NotificationProviderConfig.objects.create(tenant=self.tenant, channel=NotificationChannel.EMAIL, provider="STUB", is_active=True)
        gateway = resolve_gateway(tenant=self.tenant, channel=NotificationChannel.EMAIL)
        self.assertEqual(type(gateway).__name__, "StubEmailGateway")

    def test_unsupported_provider_raises(self):
        NotificationProviderConfig.objects.create(tenant=self.tenant, channel=NotificationChannel.SMS, provider="TWILIO", is_active=True)
        with self.assertRaises(ValidationError):
            resolve_gateway(tenant=self.tenant, channel=NotificationChannel.SMS)

    def test_in_app_always_resolves_to_the_internal_gateway_regardless_of_provider_rows(self):
        gateway = resolve_gateway(tenant=self.tenant, channel=NotificationChannel.IN_APP)
        self.assertEqual(type(gateway).__name__, "InAppGateway")


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

    def _make_template(self, *, channel=NotificationChannel.SMS, code="TEST_TEMPLATE", body="Notification", subject=""):
        return create_notification_template(user=self.admin, tenant=self.tenant, code=code, name=code, channel=channel, body=body, subject=subject)

    def _make_rule(self, *, event_code, recipient_type, channel=NotificationChannel.SMS, template=None, enabled=True, recipient_policy=None):
        template = template or self._make_template(channel=channel, code=f"{event_code}-{recipient_type}-{channel}")
        return create_notification_rule(
            user=self.admin, tenant=self.tenant, event_code=event_code, recipient_type=recipient_type,
            channel=channel, template=template, enabled=enabled, recipient_policy=recipient_policy,
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

    def test_in_app_provider_configuration_is_rejected(self):
        with self.assertRaises(ValidationError):
            configure_provider(user=self.admin, tenant=self.tenant, channel=NotificationChannel.IN_APP, provider="STUB")


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

    def test_recipient_type_not_valid_for_event_is_rejected(self):
        # leave.request.approved only allows EMPLOYEE, not GUARDIAN.
        template = self._make_template()
        with self.assertRaises(ValidationError):
            create_notification_rule(
                user=self.admin, tenant=self.tenant, event_code="leave.request.approved",
                recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS, template=template,
            )

    def test_template_variable_outside_event_contract_is_rejected(self):
        template = self._make_template(body="Score: {{ assessment_grade }}")
        with self.assertRaises(ValidationError):
            create_notification_rule(
                user=self.admin, tenant=self.tenant, event_code="finance.payment.received",
                recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS, template=template,
            )

    def test_template_variable_within_event_contract_is_accepted(self):
        template = self._make_template(body="Dear {{ guardian_name }}, amount {{ amount }}, receipt {{ receipt_number }}")
        rule = create_notification_rule(
            user=self.admin, tenant=self.tenant, event_code="finance.payment.received",
            recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS, template=template,
        )
        self.assertEqual(rule.recipient_policy, GuardianRecipientPolicy.PRIMARY_AND_EMERGENCY)

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

    def test_recipient_policy_rejected_for_employee_rules(self):
        template = self._make_template(body="Dear {{ employee_name }}")
        with self.assertRaises(ValidationError):
            create_notification_rule(
                user=self.admin, tenant=self.tenant, event_code="leave.request.approved",
                recipient_type=NotificationRecipientType.EMPLOYEE, channel=NotificationChannel.SMS, template=template,
                recipient_policy=GuardianRecipientPolicy.PRIMARY,
            )

    def test_recipient_policy_explicit_primary_only(self):
        rule = self._make_rule(
            event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN,
            recipient_policy=GuardianRecipientPolicy.PRIMARY,
        )
        self.assertEqual(rule.recipient_policy, GuardianRecipientPolicy.PRIMARY)

    def test_duplicate_rule_for_same_event_recipient_channel_is_rejected(self):
        self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN)
        with self.assertRaises(ValidationError):
            self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN)

    def test_update_rule_revalidates_template_channel(self):
        rule = self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS)
        email_template = self._make_template(channel=NotificationChannel.EMAIL, code="EMAIL_TPL")
        with self.assertRaises(ValidationError):
            update_notification_rule(user=self.admin, tenant=self.tenant, rule=rule, template=email_template)

    def test_update_rule_revalidates_template_contract(self):
        rule = self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS)
        bad_template = self._make_template(channel=NotificationChannel.SMS, code="BAD_TPL", body="{{ assessment_grade }}")
        with self.assertRaises(ValidationError):
            update_notification_rule(user=self.admin, tenant=self.tenant, rule=rule, template=bad_template)

    def test_update_recipient_policy(self):
        rule = self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN)
        updated = update_notification_rule(user=self.admin, tenant=self.tenant, rule=rule, recipient_policy=GuardianRecipientPolicy.PRIMARY)
        self.assertEqual(updated.recipient_policy, GuardianRecipientPolicy.PRIMARY)


class RecipientResolverTests(NotificationFoundationTests):
    def setUp(self):
        super().setUp()
        self.student = Student.objects.create(tenant=self.tenant, admission_number="S-001", first_name="Kim", last_name="Otieno")
        self.primary_guardian = Guardian.objects.create(tenant=self.tenant, first_name="Ann", last_name="Otieno", phone_number="0700000001", email="ann@example.com")
        self.emergency_guardian = Guardian.objects.create(tenant=self.tenant, first_name="Cy", last_name="Otieno", phone_number="0700000003", email="cy@example.com")
        self.non_primary_guardian = Guardian.objects.create(tenant=self.tenant, first_name="Ben", last_name="Otieno", phone_number="0700000002", email="ben@example.com")
        StudentGuardian.objects.create(tenant=self.tenant, student=self.student, guardian=self.primary_guardian, relationship="Mother", is_primary=True)
        StudentGuardian.objects.create(tenant=self.tenant, student=self.student, guardian=self.emergency_guardian, relationship="Aunt", is_emergency_contact=True)
        StudentGuardian.objects.create(tenant=self.tenant, student=self.student, guardian=self.non_primary_guardian, relationship="Uncle", is_primary=False, is_emergency_contact=False)

    def test_primary_and_emergency_policy_returns_both(self):
        results = _resolve_guardian_recipients(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient_refs={"student": self.student},
            policy=GuardianRecipientPolicy.PRIMARY_AND_EMERGENCY,
        )
        self.assertEqual(sorted(contact for contact, _ in results), ["0700000001", "0700000003"])

    def test_primary_only_policy_excludes_emergency_contact(self):
        results = _resolve_guardian_recipients(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient_refs={"student": self.student},
            policy=GuardianRecipientPolicy.PRIMARY,
        )
        self.assertEqual([contact for contact, _ in results], ["0700000001"])

    def test_guardian_resolver_skips_blank_contact(self):
        self.primary_guardian.email = ""
        self.primary_guardian.save(update_fields=["email"])
        results = _resolve_guardian_recipients(
            tenant=self.tenant, channel=NotificationChannel.EMAIL, recipient_refs={"student": self.student},
            policy=GuardianRecipientPolicy.PRIMARY,
        )
        self.assertEqual(results, [])

    def test_employee_resolver_skips_in_app_without_linked_account(self):
        employee = Employee.objects.create(
            tenant=self.tenant, employee_number="E-001", first_name="Joy", last_name="Wanjiru", job_title="Teacher",
            employment_type=EmploymentType.PERMANENT, hire_date=date(2020, 1, 1), phone_number="0711111111", email="joy@example.com",
        )
        results = _resolve_employee_recipients(tenant=self.tenant, channel=NotificationChannel.IN_APP, recipient_refs={"employee": employee}, policy="")
        self.assertEqual(results, [])
        results = _resolve_employee_recipients(tenant=self.tenant, channel=NotificationChannel.SMS, recipient_refs={"employee": employee}, policy="")
        self.assertEqual([contact for contact, _ in results], ["0711111111"])


class PublishNotificationEventTests(NotificationFoundationTests):
    """publish_notification_event now only writes a durable NotificationEvent
    -- no rule/recipient/template resolution happens here, so none of that
    can fail this call. See ExpandNotificationEventTests for the rest.
    """

    def setUp(self):
        super().setUp()
        self.student = Student.objects.create(tenant=self.tenant, admission_number="S-100", first_name="Lee", last_name="Achieng")

    def test_publish_creates_a_durable_event(self):
        event = publish_notification_event(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:1",
            context={"amount": "500", "receipt_number": "R-1"}, recipient_refs={"student": self.student},
        )
        self.assertIsInstance(event, NotificationEvent)
        self.assertEqual(event.recipient_refs, {"student": str(self.student.id)})

    def test_republishing_the_same_dedupe_key_is_idempotent(self):
        first = publish_notification_event(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:1",
            context={}, recipient_refs={"student": self.student},
        )
        second = publish_notification_event(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:1",
            context={}, recipient_refs={"student": self.student},
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(NotificationEvent.objects.count(), 1)

    def test_reused_dedupe_key_with_different_context_is_rejected(self):
        publish_notification_event(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:1",
            context={"amount": "500"}, recipient_refs={"student": self.student},
        )
        with self.assertRaises(ValidationError):
            publish_notification_event(
                tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:1",
                context={"amount": "999"}, recipient_refs={"student": self.student},
            )

    def test_unknown_event_code_raises(self):
        with self.assertRaises(ValueError):
            publish_notification_event(
                tenant=self.tenant, event_code="not.a.real.event", dedupe_key="x:1", context={}, recipient_refs={},
            )

    def test_unexpected_recipient_ref_raises(self):
        with self.assertRaises(ValueError):
            publish_notification_event(
                tenant=self.tenant, event_code="finance.payment.received", dedupe_key="x:2", context={},
                recipient_refs={"employee": self.student},
            )

    def test_a_bad_template_never_blocks_publication(self):
        """The whole point of #1: publish_notification_event can't fail
        because of tenant notification misconfiguration -- there's no rule
        lookup here at all to fail against.
        """
        self._enable_channel(NotificationChannel.SMS)
        bad_template = self._make_template(body="{{ this_variable_does_not_exist }}")
        with self.assertRaises(ValidationError):
            # Rule *creation* still validates the contract -- this is expected to fail here...
            create_notification_rule(
                user=self.admin, tenant=self.tenant, event_code="finance.payment.received",
                recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS, template=bad_template,
            )
        # ...so no rule exists at all, and publish still succeeds regardless.
        event = publish_notification_event(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:3",
            context={}, recipient_refs={"student": self.student},
        )
        self.assertIsInstance(event, NotificationEvent)


class ExpandNotificationEventTests(NotificationFoundationTests):
    def setUp(self):
        super().setUp()
        self.student = Student.objects.create(tenant=self.tenant, admission_number="S-100", first_name="Lee", last_name="Achieng")
        self.guardian = Guardian.objects.create(tenant=self.tenant, first_name="Rose", last_name="Achieng", phone_number="0700000009", email="rose@example.com")
        StudentGuardian.objects.create(tenant=self.tenant, student=self.student, guardian=self.guardian, relationship="Mother", is_primary=True)

    def _publish(self, dedupe_key, context=None):
        return publish_notification_event(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key=dedupe_key,
            context=context or {}, recipient_refs={"student": self.student}, actor=self.admin,
        )

    def test_no_rows_when_tenant_notifications_are_disabled(self):
        configure_communication_setup(user=self.admin, tenant=self.tenant, notifications_enabled=False)
        self._enable_channel(NotificationChannel.SMS)
        self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS)
        event = self._publish("payment-received:1")
        self.assertEqual(expand_notification_event(event=event), [])
        self.assertEqual(NotificationOutbox.objects.count(), 0)

    def test_disabled_channel_is_skipped_others_still_created(self):
        self._enable_channel(NotificationChannel.EMAIL)  # SMS left disabled
        self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS)
        self._make_rule(
            event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.EMAIL,
            template=self._make_template(channel=NotificationChannel.EMAIL, code="EMAIL_RULE", body="Hi {{ guardian_name }}"),
        )
        event = self._publish("payment-received:1")
        created = expand_notification_event(event=event)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].channel, NotificationChannel.EMAIL)

    def test_rendered_content_merges_resolver_and_caller_context(self):
        self._enable_channel(NotificationChannel.SMS)
        self._make_rule(
            event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS,
            template=self._make_template(code="RENDER_TPL", body="Dear {{ guardian_name }}, payment of {{ amount }} received"),
        )
        event = self._publish("payment-received:2", context={"amount": "500"})
        created = expand_notification_event(event=event)
        self.assertEqual(created[0].context["body"], "Dear Rose Achieng, payment of 500 received")

    def test_missing_context_variable_skips_the_rule_and_is_logged_not_raised(self):
        self._enable_channel(NotificationChannel.SMS)
        rule = self._make_rule(
            event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS,
            template=self._make_template(code="AMOUNT_TPL", body="Amount: {{ amount }}"),
        )
        event = self._publish("payment-received:3", context={})  # amount omitted at runtime
        created = expand_notification_event(event=event)
        self.assertEqual(created, [])
        log = ActivityEvent.objects.get(action="notification.rule_expansion_failed")
        self.assertEqual(log.resource_id, str(rule.id))

    def test_a_bad_rule_never_blocks_a_sibling_rule(self):
        self._enable_channel(NotificationChannel.SMS)
        self._enable_channel(NotificationChannel.EMAIL)
        self._make_rule(
            event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS,
            template=self._make_template(code="BAD_TPL", body="Amount: {{ amount }}"),  # will fail at render time
        )
        self._make_rule(
            event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.EMAIL,
            template=self._make_template(channel=NotificationChannel.EMAIL, code="GOOD_TPL", body="Hi {{ guardian_name }}"),
        )
        event = self._publish("payment-received:4", context={})
        created = expand_notification_event(event=event)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].channel, NotificationChannel.EMAIL)

    def test_inactive_template_is_skipped(self):
        self._enable_channel(NotificationChannel.SMS)
        template = self._make_template(code="INACTIVE_TPL", body="Hi {{ guardian_name }}")
        self._make_rule(event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS, template=template)
        update_notification_template(user=self.admin, tenant=self.tenant, template=template, is_active=False)
        event = self._publish("payment-received:5")
        self.assertEqual(expand_notification_event(event=event), [])

    def test_recipient_policy_primary_only_excludes_emergency_contact(self):
        emergency_guardian = Guardian.objects.create(tenant=self.tenant, first_name="Cy", last_name="Achieng", phone_number="0700000010")
        StudentGuardian.objects.create(tenant=self.tenant, student=self.student, guardian=emergency_guardian, relationship="Aunt", is_emergency_contact=True)
        self._enable_channel(NotificationChannel.SMS)
        self._make_rule(
            event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS,
            template=self._make_template(code="POLICY_TPL", body="Hi {{ guardian_name }}"), recipient_policy=GuardianRecipientPolicy.PRIMARY,
        )
        event = self._publish("payment-received:6")
        created = expand_notification_event(event=event)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].recipient, "0700000009")  # the primary guardian, not the emergency-only one

    def test_republishing_and_expanding_the_same_event_is_idempotent(self):
        self._enable_channel(NotificationChannel.SMS)
        self._make_rule(
            event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS,
            template=self._make_template(code="IDEMPOTENT_TPL", body="Hi {{ guardian_name }}"),
        )
        event = self._publish("payment-received:7")
        expand_notification_event(event=event)
        expand_notification_event(event=event)
        self.assertEqual(NotificationOutbox.objects.count(), 1)

    def test_task_wiring_expands_a_claimed_event_end_to_end(self):
        self._enable_channel(NotificationChannel.SMS)
        self._make_rule(
            event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS,
            template=self._make_template(code="TASK_TPL", body="Hi {{ guardian_name }}"),
        )
        event = self._publish("payment-received:8")
        expand_pending_notification_events()
        event.refresh_from_db()
        self.assertEqual(event.status, DurableWorkStatus.PROCESSED)
        self.assertEqual(NotificationOutbox.objects.count(), 1)

    def test_unresolvable_recipient_ref_fails_the_event_without_crashing(self):
        self._enable_channel(NotificationChannel.SMS)
        self._make_rule(
            event_code="finance.payment.received", recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS,
            template=self._make_template(code="GHOST_TPL", body="Hi {{ guardian_name }}"),
        )
        event = self._publish("payment-received:9")
        self.student.delete()
        self.assertEqual(expand_notification_event(event=event), [])
        self.assertTrue(ActivityEvent.objects.filter(action="notification.event_expansion_failed").exists())


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
        source_outbox = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.IN_APP, recipient_user_id=self.admin.id,
            message_type="x", idempotency_key="x:1", context={"subject": "Hi", "body": "Body"},
        )
        notification = UserNotification.objects.create(
            tenant=self.tenant, user=self.admin, source_notification=source_outbox, title="Hi", message="Body",
        )
        with self.assertRaises(ValidationError):
            mark_user_notification_read(user=other_user, notification=notification)
        updated = mark_user_notification_read(user=self.admin, notification=notification)
        self.assertIsNotNone(updated.read_at)
        first_read_at = updated.read_at
        updated_again = mark_user_notification_read(user=self.admin, notification=notification)
        self.assertEqual(updated_again.read_at, first_read_at)

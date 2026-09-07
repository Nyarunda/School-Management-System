"""Real PostgreSQL transactions; SQLite deliberately cannot validate these
tests. The generic "two concurrent claim_due calls never double-claim the
same row" case already exists at the durable_work level
(apps/activity/test_durable_work_concurrency.py, exercised directly against
NotificationOutbox) -- this file covers what's new in this milestone:
publish_notification_event's idempotency under real concurrent writers,
which enqueue_notification's IntegrityError->replay handling is meant to
serialize.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import connection, connections
from django.test import TransactionTestCase

from apps.guardians.models import Guardian, StudentGuardian
from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import CommunicationChannel, NotificationChannel, NotificationOutbox, NotificationRecipientType
from .services import create_notification_rule, create_notification_template, publish_notification_event


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class PublishNotificationEventConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test School", slug="test-school")
        self.admin = User.objects.create_user(username="admin", password="secret")
        role = Role.objects.create(
            tenant=self.tenant, name="Comms Admin",
            permissions=["notifications.setup.manage", "notifications.templates.manage", "notifications.rules.manage"],
        )
        Membership.objects.create(tenant=self.tenant, user=self.admin, role=role)
        CommunicationChannel.objects.create(tenant=self.tenant, channel=NotificationChannel.SMS, enabled=True)
        template = create_notification_template(
            user=self.admin, tenant=self.tenant, code="RACE_TPL", name="Race", channel=NotificationChannel.SMS, body="Hi {{ guardian_name }}",
        )
        create_notification_rule(
            user=self.admin, tenant=self.tenant, event_code="finance.payment.received",
            recipient_type=NotificationRecipientType.GUARDIAN, channel=NotificationChannel.SMS, template=template,
        )
        self.student = Student.objects.create(tenant=self.tenant, admission_number="S-900", first_name="Amina", last_name="Hassan")
        self.guardian = Guardian.objects.create(tenant=self.tenant, first_name="Fatima", last_name="Hassan", phone_number="0700000099")
        StudentGuardian.objects.create(tenant=self.tenant, student=self.student, guardian=self.guardian, relationship="Mother", is_primary=True)

    def _publish(self, barrier):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            barrier.wait(timeout=6)
            created = publish_notification_event(
                tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:race",
                context={}, recipient_refs={"student": self.student},
            )
            return [row.pk for row in created]
        finally:
            connections.close_all()

    def test_concurrent_publish_with_the_same_dedupe_key_resolves_to_one_row(self):
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._publish, barrier) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]

        all_pks = {pk for result in results for pk in result}
        self.assertEqual(len(all_pks), 1)
        self.assertEqual(NotificationOutbox.objects.filter(tenant=self.tenant).count(), 1)

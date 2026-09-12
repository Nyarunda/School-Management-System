"""Real PostgreSQL transactions; SQLite deliberately cannot validate these
tests. The generic "two concurrent claim_due calls never double-claim the
same row" case already exists at the durable_work level
(apps/activity/test_durable_work_concurrency.py, exercised directly against
NotificationOutbox); this file adds the same coverage for NotificationEvent
(a new DurableWorkModel subclass since Milestone 18.1) plus what's specific
to this app: publish_notification_event's dedupe_key idempotency, and
expand_notification_event's own idempotency when two workers race to expand
the same already-published event.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import connection, connections
from django.test import TransactionTestCase

from apps.activity.durable_work import claim_due
from apps.guardians.models import Guardian, StudentGuardian
from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from .models import CommunicationChannel, NotificationChannel, NotificationEvent, NotificationOutbox, NotificationRecipientType
from .services import create_notification_rule, create_notification_template, expand_notification_event, publish_notification_event


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL transaction semantics")
class NotificationConcurrencyTests(TransactionTestCase):
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

    def _with_bounded_connection(self, barrier, fn):
        connections.close_all()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8s'")
                cursor.execute("SET lock_timeout = '6s'")
            barrier.wait(timeout=6)
            return fn()
        finally:
            connections.close_all()

    def test_concurrent_publish_with_the_same_dedupe_key_resolves_to_one_event(self):
        barrier = Barrier(2)

        def publish():
            event = publish_notification_event(
                tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:race",
                context={}, recipient_refs={"student": self.student},
            )
            return event.pk

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._with_bounded_connection, barrier, publish) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]

        self.assertEqual(len(set(results)), 1)
        self.assertEqual(NotificationEvent.objects.filter(tenant=self.tenant).count(), 1)

    def test_concurrent_expansion_of_the_same_event_creates_exactly_one_outbox_row(self):
        event = publish_notification_event(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="payment-received:expand-race",
            context={}, recipient_refs={"student": self.student},
        )
        barrier = Barrier(2)

        def expand():
            return len(expand_notification_event(event=event))

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._with_bounded_connection, barrier, expand) for _ in range(2)]
            [future.result(timeout=15) for future in futures]

        self.assertEqual(NotificationOutbox.objects.filter(tenant=self.tenant).count(), 1)

    def test_two_concurrent_claims_never_double_claim_the_same_event(self):
        row_a = NotificationEvent.objects.create(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="claim-a", recipient_refs={},
        )
        row_b = NotificationEvent.objects.create(
            tenant=self.tenant, event_code="finance.payment.received", dedupe_key="claim-b", recipient_refs={},
        )
        barrier = Barrier(2)

        def claim_one():
            claimed = claim_due(NotificationEvent.objects.filter(tenant=self.tenant), limit=1)
            return [row.pk for row in claimed]

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._with_bounded_connection, barrier, claim_one) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]

        claimed_pks = [pk for result in results for pk in result]
        self.assertCountEqual(claimed_pks, [row_a.pk, row_b.pk])

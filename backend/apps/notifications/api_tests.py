from django.test import TestCase
from rest_framework.test import APIClient

from apps.tenancy.models import Membership, Role, Tenant, User

from .models import NotificationChannel, NotificationOutbox
from .services import enqueue_notification


class NotificationsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")

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

        self.viewer_role = Role.objects.create(tenant=self.tenant, name="Viewer Only", permissions=["notifications.setup.view"])
        self.viewer = User.objects.create_user(username="viewer", password="secret")
        Membership.objects.create(tenant=self.tenant, user=self.viewer, role=self.viewer_role)

        self.client.force_authenticate(self.admin)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def test_missing_tenant_header_is_rejected(self):
        response = self.client.get("/api/v1/notifications/setup/")
        self.assertEqual(response.status_code, 404)

    def test_setup_get_and_patch(self):
        get_response = self.client.get("/api/v1/notifications/setup/", **self.headers())
        self.assertEqual(get_response.status_code, 200)
        self.assertTrue(get_response.data["notifications_enabled"])

        patch_response = self.client.patch(
            "/api/v1/notifications/setup/", {"notifications_enabled": False}, format="json", **self.headers(),
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertFalse(patch_response.data["notifications_enabled"])

    def test_viewer_cannot_patch_setup(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.patch(
            "/api/v1/notifications/setup/", {"notifications_enabled": False}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 403)

    def test_channel_get_and_patch(self):
        get_response = self.client.get("/api/v1/notifications/channels/SMS/", **self.headers())
        self.assertEqual(get_response.status_code, 200)
        self.assertFalse(get_response.data["enabled"])

        patch_response = self.client.patch(
            "/api/v1/notifications/channels/SMS/", {"enabled": True}, format="json", **self.headers(),
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertTrue(patch_response.data["enabled"])

    def test_provider_configure_never_returns_secrets(self):
        response = self.client.post(
            "/api/v1/notifications/providers/",
            {"channel": "SMS", "provider": "STUB", "sender_id": "SCHOOL", "api_key": "secret-key", "api_secret": "secret-value"},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("api_key", response.data)
        self.assertNotIn("api_secret", response.data)
        self.assertNotIn("encrypted_api_key", response.data)
        self.assertEqual(response.data["sender_id"], "SCHOOL")

        list_response = self.client.get("/api/v1/notifications/providers/", **self.headers())
        self.assertEqual(list_response.status_code, 200)
        self.assertNotIn("encrypted_api_key", list_response.data[0])

    def test_provider_configure_rejects_in_app(self):
        response = self.client.post(
            "/api/v1/notifications/providers/", {"channel": "IN_APP", "provider": "STUB"}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)

    def create_template(self, **overrides):
        payload = {"code": "PAYMENT_SMS", "name": "Payment SMS", "channel": "SMS", "body": "Hi {{ guardian_name }}"}
        payload.update(overrides)
        return self.client.post("/api/v1/notifications/templates/", payload, format="json", **self.headers())

    def test_template_crud(self):
        create_response = self.create_template()
        self.assertEqual(create_response.status_code, 201)
        template_id = create_response.data["id"]

        patch_response = self.client.patch(
            f"/api/v1/notifications/templates/{template_id}/", {"name": "Updated"}, format="json", **self.headers(),
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.data["name"], "Updated")

        list_response = self.client.get("/api/v1/notifications/templates/", **self.headers())
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["count"], 1)

    def test_rule_crud(self):
        template_id = self.create_template().data["id"]
        create_response = self.client.post(
            "/api/v1/notifications/rules/",
            {"event_code": "finance.payment.received", "recipient_type": "GUARDIAN", "channel": "SMS", "template": template_id},
            format="json", **self.headers(),
        )
        self.assertEqual(create_response.status_code, 201)
        rule_id = create_response.data["id"]
        self.assertEqual(create_response.data["recipient_policy"], "PRIMARY_AND_EMERGENCY")  # default for GUARDIAN

        patch_response = self.client.patch(
            f"/api/v1/notifications/rules/{rule_id}/", {"enabled": False}, format="json", **self.headers(),
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertFalse(patch_response.data["enabled"])

    def test_rule_rejects_unknown_event_code(self):
        template_id = self.create_template().data["id"]
        response = self.client.post(
            "/api/v1/notifications/rules/",
            {"event_code": "not.a.real.event", "recipient_type": "GUARDIAN", "channel": "SMS", "template": template_id},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)

    def test_outbox_list_detail_and_retry(self):
        outbox = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.SMS, recipient="0700000000",
            message_type="x", idempotency_key="x:1", context={"subject": "", "body": "hi"},
        )
        outbox.status = "FAILED"
        outbox.save(update_fields=["status"])

        list_response = self.client.get("/api/v1/notifications/outbox/", **self.headers())
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["count"], 1)

        detail_response = self.client.get(f"/api/v1/notifications/outbox/{outbox.id}/", **self.headers())
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["status"], "FAILED")

        retry_response = self.client.post(f"/api/v1/notifications/outbox/{outbox.id}/retry/", **self.headers())
        self.assertEqual(retry_response.status_code, 200)
        self.assertEqual(retry_response.data["status"], "PENDING")

        deliveries_response = self.client.get(f"/api/v1/notifications/outbox/{outbox.id}/deliveries/", **self.headers())
        self.assertEqual(deliveries_response.status_code, 200)
        self.assertEqual(deliveries_response.data, [])

    def test_inbox_list_and_mark_read(self):
        from .models import UserNotification

        source_outbox = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.IN_APP, recipient_user_id=self.admin.id,
            message_type="x", idempotency_key="inbox:1", context={"subject": "Hi", "body": "Body"},
        )
        notification = UserNotification.objects.create(
            tenant=self.tenant, user=self.admin, source_notification=source_outbox, title="Hi", message="Body",
        )

        list_response = self.client.get("/api/v1/notifications/inbox/", **self.headers())
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["count"], 1)

        read_response = self.client.post(f"/api/v1/notifications/inbox/{notification.id}/read/", **self.headers())
        self.assertEqual(read_response.status_code, 200)
        self.assertIsNotNone(read_response.data["read_at"])

    def test_inbox_only_shows_the_authenticated_users_own_notifications(self):
        from .models import UserNotification

        source_outbox = enqueue_notification(
            tenant=self.tenant, channel=NotificationChannel.IN_APP, recipient_user_id=self.viewer.id,
            message_type="x", idempotency_key="inbox:2", context={"subject": "Hi", "body": "Body"},
        )
        UserNotification.objects.create(
            tenant=self.tenant, user=self.viewer, source_notification=source_outbox, title="Not mine", message="Body",
        )
        response = self.client.get("/api/v1/notifications/inbox/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

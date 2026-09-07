from django.test import TestCase
from rest_framework.test import APIClient

from apps.tenancy.models import Membership, Role, Tenant, User


class DocumentSetupApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        self.admin = User.objects.create_user(username="admin", password="secret")
        self.admin_role = Role.objects.create(
            tenant=self.tenant, name="Admin", permissions=["documents.setup.view", "documents.setup.manage"],
        )
        Membership.objects.create(tenant=self.tenant, user=self.admin, role=self.admin_role)

        self.viewer = User.objects.create_user(username="viewer", password="secret")
        self.viewer_role = Role.objects.create(tenant=self.tenant, name="Viewer", permissions=["documents.setup.view"])
        Membership.objects.create(tenant=self.tenant, user=self.viewer, role=self.viewer_role)

        self.client.force_authenticate(self.admin)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def test_missing_tenant_header_is_rejected(self):
        response = self.client.get("/api/v1/documents/setup/")
        self.assertEqual(response.status_code, 404)

    def test_get_defaults_when_unconfigured(self):
        response = self.client.get("/api/v1/documents/setup/", **self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["default_retention_days"])

    def test_patch_configures_retention(self):
        response = self.client.patch(
            "/api/v1/documents/setup/", {"default_retention_days": 90}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["default_retention_days"], 90)

    def test_viewer_cannot_patch(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.patch(
            "/api/v1/documents/setup/", {"default_retention_days": 90}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 403)

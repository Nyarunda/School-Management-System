from django.test import TestCase
from rest_framework.test import APIClient

from .models import Membership, Role, Tenant, User


class SessionApiTests(TestCase):
    def test_session_bootstrap_returns_active_tenant_and_permissions(self):
        tenant = Tenant.objects.create(name="School A", slug="school-a")
        user = User.objects.create_user(username="admin", password="secret", first_name="Amina")
        role = Role.objects.create(tenant=tenant, name="Bursar", permissions=["finance.invoice.view"])
        Membership.objects.create(tenant=tenant, user=user, role=role)
        client = APIClient()
        client.force_authenticate(user)

        response = client.get("/api/v1/session/", HTTP_X_TENANT_SLUG="school-a")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["active_tenant"]["slug"], "school-a")
        self.assertEqual(response.data["permissions"], ["finance.invoice.view"])
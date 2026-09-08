from django.test import TestCase
from rest_framework.test import APIClient

from apps.tenancy.models import Membership, Role, Tenant, User

from .models import SubscriptionPlan, TenantSubscription


class PlatformApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")

        self.superuser = User.objects.create_user(username="root", password="secret", is_superuser=True, is_staff=True)

        self.ordinary_admin = User.objects.create_user(username="admin", password="secret")
        role = Role.objects.create(tenant=self.tenant, name="Admin", permissions=["finance.view"])
        Membership.objects.create(tenant=self.tenant, user=self.ordinary_admin, role=role)

    def test_ordinary_tenant_member_cannot_reach_platform_endpoints(self):
        self.client.force_authenticate(self.ordinary_admin)
        response = self.client.get("/api/v1/platform/modules/")
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get("/api/v1/platform/modules/")
        self.assertEqual(response.status_code, 403)

    def test_module_catalogue_is_listed(self):
        self.client.force_authenticate(self.superuser)
        response = self.client.get("/api/v1/platform/modules/")
        self.assertEqual(response.status_code, 200)
        codes = {entry["code"] for entry in response.data}
        self.assertIn("finance", codes)

    def test_create_and_fetch_plan(self):
        self.client.force_authenticate(self.superuser)
        response = self.client.post(
            "/api/v1/platform/plans/", {"name": "Standard", "module_codes": ["finance", "attendance"]}, format="json",
        )
        self.assertEqual(response.status_code, 201)
        plan_id = response.data["id"]

        detail = self.client.get(f"/api/v1/platform/plans/{plan_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(set(detail.data["module_codes"]), {"finance", "attendance"})

    def test_create_plan_rejects_an_unknown_module_code(self):
        self.client.force_authenticate(self.superuser)
        response = self.client.post(
            "/api/v1/platform/plans/", {"name": "Bogus", "module_codes": ["not_a_real_module"]}, format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_assign_plan_to_tenant_and_read_back_entitlements(self):
        self.client.force_authenticate(self.superuser)
        plan_response = self.client.post(
            "/api/v1/platform/plans/", {"name": "Standard", "module_codes": ["finance"]}, format="json",
        )
        plan_id = plan_response.data["id"]

        assign_response = self.client.put(
            f"/api/v1/platform/tenants/{self.tenant.id}/subscription/", {"plan_id": plan_id}, format="json",
        )
        self.assertEqual(assign_response.status_code, 200)
        self.assertEqual(assign_response.data["enabled_modules"], ["finance"])

        get_response = self.client.get(f"/api/v1/platform/tenants/{self.tenant.id}/subscription/")
        self.assertEqual(get_response.data["plan"]["name"], "Standard")

    def test_module_override_lifecycle(self):
        self.client.force_authenticate(self.superuser)
        put_response = self.client.put(
            f"/api/v1/platform/tenants/{self.tenant.id}/overrides/finance/", {"is_enabled": False}, format="json",
        )
        self.assertEqual(put_response.status_code, 200)
        self.assertFalse(put_response.data["is_enabled"])

        list_response = self.client.get(f"/api/v1/platform/tenants/{self.tenant.id}/overrides/")
        self.assertEqual(len(list_response.data), 1)

        # PUT is an upsert -- calling it again updates the same row rather
        # than creating a second one.
        second_put = self.client.put(
            f"/api/v1/platform/tenants/{self.tenant.id}/overrides/finance/", {"is_enabled": True}, format="json",
        )
        self.assertEqual(second_put.status_code, 200)
        self.assertEqual(len(self.client.get(f"/api/v1/platform/tenants/{self.tenant.id}/overrides/").data), 1)

        delete_response = self.client.delete(f"/api/v1/platform/tenants/{self.tenant.id}/overrides/finance/")
        self.assertEqual(delete_response.status_code, 204)
        self.assertEqual(
            len(self.client.get(f"/api/v1/platform/tenants/{self.tenant.id}/overrides/").data), 0,
        )

    def test_override_boolean_string_is_parsed_correctly(self):
        """The bug this guards against: bool("false") is True in Python --
        the write path must go through a real BooleanField, not a bare
        bool(request.data.get(...)) cast.
        """
        self.client.force_authenticate(self.superuser)
        response = self.client.put(
            f"/api/v1/platform/tenants/{self.tenant.id}/overrides/finance/", {"is_enabled": "false"}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_enabled"])
        from apps.platform.services import get_enabled_modules
        self.assertNotIn("finance", get_enabled_modules(self.tenant))

    def test_plan_deletion_is_blocked_while_assigned(self):
        self.client.force_authenticate(self.superuser)
        plan_response = self.client.post(
            "/api/v1/platform/plans/", {"name": "Standard", "module_codes": ["finance"]}, format="json",
        )
        plan_id = plan_response.data["id"]
        self.client.put(f"/api/v1/platform/tenants/{self.tenant.id}/subscription/", {"plan_id": plan_id}, format="json")

        delete_response = self.client.delete(f"/api/v1/platform/plans/{plan_id}/")
        self.assertEqual(delete_response.status_code, 400)


class SessionEntitlementTests(TestCase):
    def test_session_reports_enabled_modules_for_the_active_tenant(self):
        tenant = Tenant.objects.create(name="School A", slug="school-a")
        user = User.objects.create_user(username="admin", password="secret")
        role = Role.objects.create(tenant=tenant, name="Admin", permissions=[])
        Membership.objects.create(tenant=tenant, user=user, role=role)

        client = APIClient()
        client.force_authenticate(user)
        response = client.get("/api/v1/session/", HTTP_X_TENANT_SLUG="school-a")
        self.assertEqual(response.status_code, 200)
        subscription = TenantSubscription.objects.get(tenant=tenant)
        self.assertEqual(
            response.data["active_tenant"]["enabled_modules"], sorted(subscription.plan.module_codes),
        )

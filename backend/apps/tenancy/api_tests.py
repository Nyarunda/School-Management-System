from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.notifications.models import NotificationOutbox

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


class AuthApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="admin", password="secret")

    def test_login_with_valid_credentials_returns_a_token(self):
        response = self.client.post("/api/v1/auth/login/", {"username": "admin", "password": "secret"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["token"], Token.objects.get(user=self.user).key)

    def test_login_with_invalid_credentials_is_rejected(self):
        response = self.client.post("/api/v1/auth/login/", {"username": "admin", "password": "wrong"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_issued_token_authenticates_subsequent_requests(self):
        token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.get("/api/v1/session/")
        self.assertEqual(response.status_code, 200)

    def test_logout_deletes_the_token(self):
        token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.post("/api/v1/auth/logout/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Token.objects.filter(user=self.user).exists())
        # The now-deleted token can no longer authenticate.
        response = self.client.get("/api/v1/session/")
        self.assertEqual(response.status_code, 401)

    def test_unauthenticated_logout_is_rejected(self):
        response = self.client.post("/api/v1/auth/logout/")
        self.assertEqual(response.status_code, 401)


class TenancyAdminApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.admin_role = Role.objects.create(
            tenant=self.school_a, name="Administrator",
            permissions=["tenancy.membership.manage", "tenancy.role.manage", "tenancy.membership.view", "tenancy.role.view", "students.view"],
        )
        self.admin_user = User.objects.create_user(username="admin-a", password="secret")
        self.admin_membership = Membership.objects.create(tenant=self.school_a, user=self.admin_user, role=self.admin_role)
        self.viewer_user = User.objects.create_user(username="viewer-a", password="secret")
        self.viewer_role = Role.objects.create(tenant=self.school_a, name="Viewer", permissions=["students.view"])
        Membership.objects.create(tenant=self.school_a, user=self.viewer_user, role=self.viewer_role)
        self.client.force_authenticate(self.admin_user)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-a"}

    def test_permission_catalogue_is_listed(self):
        response = self.client.get("/api/v1/tenancy/permissions/", **self.headers())
        self.assertEqual(response.status_code, 200)
        codes = {entry["code"] for entry in response.data}
        self.assertIn("tenancy.membership.manage", codes)
        self.assertIn("finance.invoice.view", codes)

    def test_permission_catalogue_requires_role_view_permission(self):
        self.client.force_authenticate(self.viewer_user)
        response = self.client.get("/api/v1/tenancy/permissions/", **self.headers())
        self.assertEqual(response.status_code, 403)

    def test_role_create_and_list(self):
        response = self.client.post(
            "/api/v1/tenancy/roles/", {"name": "Registrar", "permissions": ["students.view"]}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 201)
        role_id = response.data["id"]

        list_response = self.client.get("/api/v1/tenancy/roles/", **self.headers())
        self.assertEqual(list_response.status_code, 200)
        names = {entry["name"] for entry in list_response.data["results"]}
        self.assertIn("Registrar", names)

        detail_response = self.client.get(f"/api/v1/tenancy/roles/{role_id}/", **self.headers())
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["permissions"], ["students.view"])

    def test_role_create_rejects_ungrantable_permission(self):
        response = self.client.post(
            "/api/v1/tenancy/roles/", {"name": "Bursar", "permissions": ["finance.invoice.view"]}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)

    def test_role_write_endpoints_require_manage_permission(self):
        self.client.force_authenticate(self.viewer_user)
        response = self.client.post(
            "/api/v1/tenancy/roles/", {"name": "Registrar", "permissions": []}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 403)

    def test_role_update_and_delete(self):
        create_response = self.client.post(
            "/api/v1/tenancy/roles/", {"name": "Registrar", "permissions": ["students.view"]}, format="json", **self.headers(),
        )
        role_id = create_response.data["id"]

        patch_response = self.client.patch(f"/api/v1/tenancy/roles/{role_id}/", {"name": "Renamed"}, format="json", **self.headers())
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.data["name"], "Renamed")

        delete_response = self.client.delete(f"/api/v1/tenancy/roles/{role_id}/", **self.headers())
        self.assertEqual(delete_response.status_code, 204)

    def test_role_from_another_tenant_is_not_found(self):
        foreign_role = Role.objects.create(tenant=self.school_b, name="Foreign", permissions=[])
        response = self.client.get(f"/api/v1/tenancy/roles/{foreign_role.id}/", **self.headers())
        self.assertEqual(response.status_code, 404)

    def test_membership_list_includes_denormalized_user_and_role(self):
        response = self.client.get("/api/v1/tenancy/memberships/", **self.headers())
        self.assertEqual(response.status_code, 200)
        usernames = {entry["user"]["username"] for entry in response.data["results"]}
        self.assertIn("admin-a", usernames)
        self.assertIn("viewer-a", usernames)

    def test_membership_from_another_tenant_is_not_found(self):
        foreign_role = Role.objects.create(tenant=self.school_b, name="Foreign", permissions=[])
        foreign_user = User.objects.create_user(username="foreign", password="secret")
        foreign_membership = Membership.objects.create(tenant=self.school_b, user=foreign_user, role=foreign_role)
        response = self.client.get(f"/api/v1/tenancy/memberships/{foreign_membership.id}/", **self.headers())
        self.assertEqual(response.status_code, 404)

    def test_membership_deactivate_and_activate(self):
        deactivate_response = self.client.post(
            f"/api/v1/tenancy/memberships/{self.admin_membership.id}/deactivate/", **self.headers(),
        )
        # Blocked -- this is the tenant's only administrator.
        self.assertEqual(deactivate_response.status_code, 400)

        viewer_membership = Membership.objects.get(tenant=self.school_a, user=self.viewer_user)
        deactivate_viewer = self.client.post(
            f"/api/v1/tenancy/memberships/{viewer_membership.id}/deactivate/", **self.headers(),
        )
        self.assertEqual(deactivate_viewer.status_code, 200)
        self.assertFalse(deactivate_viewer.data["is_active"])

        activate_response = self.client.post(f"/api/v1/tenancy/memberships/{viewer_membership.id}/activate/", **self.headers())
        self.assertEqual(activate_response.status_code, 200)
        self.assertTrue(activate_response.data["is_active"])

    def test_membership_role_update(self):
        viewer_membership = Membership.objects.get(tenant=self.school_a, user=self.viewer_user)
        new_role = Role.objects.create(tenant=self.school_a, name="Registrar", permissions=["students.view"])
        response = self.client.patch(
            f"/api/v1/tenancy/memberships/{viewer_membership.id}/", {"role": new_role.id}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role"]["name"], "Registrar")

    def test_membership_endpoints_require_manage_permission(self):
        self.client.force_authenticate(self.viewer_user)
        viewer_membership = Membership.objects.get(tenant=self.school_a, user=self.viewer_user)
        response = self.client.post(f"/api/v1/tenancy/memberships/{viewer_membership.id}/deactivate/", **self.headers())
        self.assertEqual(response.status_code, 403)

    def test_invite_and_accept_and_login_round_trip(self):
        invite_response = self.client.post(
            "/api/v1/tenancy/users/invite/",
            {"email": "new.teacher@example.com", "role": self.viewer_role.id}, format="json", **self.headers(),
        )
        self.assertEqual(invite_response.status_code, 201)

        outbox_entry = NotificationOutbox.objects.for_tenant(self.school_a).get(message_type="tenancy.user_invited")
        token = outbox_entry.context["invite_link"].split("token=")[1]

        anonymous_client = APIClient()
        accept_response = anonymous_client.post(
            "/api/v1/auth/invites/accept/", {"token": token, "password": "a-strong-passw0rd!"}, format="json",
        )
        self.assertEqual(accept_response.status_code, 200)
        issued_token = accept_response.data["token"]

        login_response = anonymous_client.post(
            "/api/v1/auth/login/", {"username": "new.teacher@example.com", "password": "a-strong-passw0rd!"}, format="json",
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(login_response.data["token"], issued_token)
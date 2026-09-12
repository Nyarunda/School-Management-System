from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.notifications.models import NotificationOutbox

from .models import Campus, Membership, Role, Tenant, User


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
        self.assertFalse(response.data["user"]["is_platform_admin"])

    def test_session_bootstrap_reports_platform_admin_for_superusers(self):
        user = User.objects.create_user(username="root", password="secret", is_superuser=True, is_staff=True)
        client = APIClient()
        client.force_authenticate(user)

        response = client.get("/api/v1/session/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["user"]["is_platform_admin"])

    def test_session_bootstrap_reports_no_platform_admin_without_any_membership(self):
        user = User.objects.create_user(username="orphan", password="secret")
        client = APIClient()
        client.force_authenticate(user)

        response = client.get("/api/v1/session/")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["active_tenant"])
        self.assertFalse(response.data["user"]["is_platform_admin"])


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

    def test_role_manage_permission_does_not_imply_role_view(self):
        # Same non-implication question as campuses, for the role catalogue
        # Invite/Reassign's role picker depends on: .manage alone must not
        # be treated as if it also grants .view.
        manage_only_role = Role.objects.create(tenant=self.school_a, name="Role Manage Only", permissions=["tenancy.role.manage"])
        manage_only_user = User.objects.create_user(username="role-manage-only", password="secret")
        Membership.objects.create(tenant=self.school_a, user=manage_only_user, role=manage_only_role)
        self.client.force_authenticate(manage_only_user)
        response = self.client.get("/api/v1/tenancy/roles/", **self.headers())
        self.assertEqual(response.status_code, 403)

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

    def test_inviting_an_existing_user_to_a_second_tenant_accepts_without_a_password(self):
        existing = User.objects.create_user(username="existing-api", email="existing-api@example.com", password="whatever")
        invite_response = self.client.post(
            "/api/v1/tenancy/users/invite/",
            {"email": "existing-api@example.com", "role": self.viewer_role.id}, format="json", **self.headers(),
        )
        self.assertEqual(invite_response.status_code, 201)
        membership_id = invite_response.data["id"]
        self.assertFalse(Membership.objects.get(pk=membership_id).is_active)

        outbox_entry = NotificationOutbox.objects.for_tenant(self.school_a).get(
            message_type="tenancy.user_invited", recipient="existing-api@example.com",
        )
        token = outbox_entry.context["invite_link"].split("token=")[1]

        anonymous_client = APIClient()
        accept_response = anonymous_client.post("/api/v1/auth/invites/accept/", {"token": token}, format="json")
        self.assertEqual(accept_response.status_code, 200)

        membership = Membership.objects.get(pk=membership_id)
        self.assertTrue(membership.is_active)
        self.assertTrue(existing.check_password("whatever"))

    def test_campus_catalogue_is_tenant_scoped_and_paginated(self):
        Campus.objects.create(tenant=self.school_a, name="Main Campus", code="MAIN")
        Campus.objects.create(tenant=self.school_a, name="North Campus", code="NORTH")
        Campus.objects.create(tenant=self.school_b, name="Foreign Campus", code="FOREIGN")

        response = self.client.get("/api/v1/tenancy/campuses/", **self.headers())

        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.data)
        self.assertEqual(response.data["count"], 2)
        names = {entry["name"] for entry in response.data["results"]}
        self.assertEqual(names, {"Main Campus", "North Campus"})
        self.assertEqual(set(response.data["results"][0].keys()), {"id", "name"})

    def test_campus_catalogue_requires_membership_view_permission(self):
        # No relevant permission at all.
        self.client.force_authenticate(self.viewer_user)
        response = self.client.get("/api/v1/tenancy/campuses/", **self.headers())
        self.assertEqual(response.status_code, 403)

        # tenancy.membership.manage alone does NOT imply .view -- confirms the
        # permission-implication question raised before implementing this
        # endpoint: manage-only holders are correctly still blocked here, so
        # a real admin role must be granted both explicitly if it needs to
        # both invite/reassign and populate this picker.
        manage_only_role = Role.objects.create(tenant=self.school_a, name="Manage Only", permissions=["tenancy.membership.manage"])
        manage_only_user = User.objects.create_user(username="manage-only", password="secret")
        Membership.objects.create(tenant=self.school_a, user=manage_only_user, role=manage_only_role)
        self.client.force_authenticate(manage_only_user)
        response = self.client.get("/api/v1/tenancy/campuses/", **self.headers())
        self.assertEqual(response.status_code, 403)

        # .view alone is sufficient.
        view_only_role = Role.objects.create(tenant=self.school_a, name="View Only", permissions=["tenancy.membership.view"])
        view_only_user = User.objects.create_user(username="view-only", password="secret")
        Membership.objects.create(tenant=self.school_a, user=view_only_user, role=view_only_role)
        self.client.force_authenticate(view_only_user)
        response = self.client.get("/api/v1/tenancy/campuses/", **self.headers())
        self.assertEqual(response.status_code, 200)

    def test_campus_catalogue_second_page_is_reachable(self):
        Campus.objects.bulk_create([Campus(tenant=self.school_a, name=f"Campus {i:03d}", code=f"C{i:03d}") for i in range(26)])

        first_page = self.client.get("/api/v1/tenancy/campuses/", **self.headers())
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.data["count"], 26)
        self.assertEqual(len(first_page.data["results"]), 25)
        self.assertIsNotNone(first_page.data["next"])

        second_page = self.client.get("/api/v1/tenancy/campuses/?page=2", **self.headers())
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(len(second_page.data["results"]), 1)
        self.assertIsNone(second_page.data["next"])

    def test_membership_serializer_distinguishes_pending_active_and_deactivated(self):
        invite_response = self.client.post(
            "/api/v1/tenancy/users/invite/",
            {"email": "pending.member@example.com", "role": self.viewer_role.id}, format="json", **self.headers(),
        )
        pending_id = invite_response.data["id"]
        pending = self.client.get(f"/api/v1/tenancy/memberships/{pending_id}/", **self.headers())
        self.assertFalse(pending.data["is_active"])
        self.assertIsNone(pending.data["invite_accepted_at"])

        outbox_entry = NotificationOutbox.objects.for_tenant(self.school_a).get(
            message_type="tenancy.user_invited", recipient="pending.member@example.com",
        )
        token = outbox_entry.context["invite_link"].split("token=")[1]
        # InviteAcceptView shares the "login" ScopedRateThrottle bucket with
        # LoginView (5/min), keyed by client IP in a cache that persists
        # across test methods within this run -- other tests in this module
        # also call accept/login, so without clearing here this can trip the
        # throttle depending on execution order/timing. Not testing the
        # throttle itself, so reset it rather than let this test be
        # order-dependent.
        from django.core.cache import cache
        cache.clear()
        accept_response = APIClient().post("/api/v1/auth/invites/accept/", {"token": token, "password": "a-strong-passw0rd!"}, format="json")
        self.assertEqual(accept_response.status_code, 200)

        active = self.client.get(f"/api/v1/tenancy/memberships/{pending_id}/", **self.headers())
        self.assertTrue(active.data["is_active"])
        self.assertIsNotNone(active.data["invite_accepted_at"])

        self.client.post(f"/api/v1/tenancy/memberships/{pending_id}/deactivate/", **self.headers())
        deactivated = self.client.get(f"/api/v1/tenancy/memberships/{pending_id}/", **self.headers())
        self.assertFalse(deactivated.data["is_active"])
        self.assertIsNotNone(deactivated.data["invite_accepted_at"])


class RoleUpdateAuthorizationTests(TestCase):
    """update_role() previously checked the entire requested permission list
    for grantability, not just what's actually changing -- so a role holding
    even one permission outside the acting admin's own grant set became
    completely unmodifiable by that admin, including fully legitimate edits.
    Fixed to check only the symmetric difference (added | removed); an
    unchanged permission is neutral regardless of who holds it. These tests
    cover the full authorization matrix that fix depends on.
    """

    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(name="School C", slug="school-c")
        # Holds tenancy.role.manage/.view and finance.invoice.view (Y) --
        # deliberately NOT finance.payment.reverse (X) or finance.invoice.issue (Z).
        self.editor_role = Role.objects.create(
            tenant=self.tenant, name="Editor",
            permissions=["tenancy.role.manage", "tenancy.role.view", "finance.invoice.view"],
        )
        self.editor_user = User.objects.create_user(username="editor-c", password="secret")
        Membership.objects.create(tenant=self.tenant, user=self.editor_user, role=self.editor_role)
        self.client.force_authenticate(self.editor_user)

    def headers(self):
        return {"HTTP_X_TENANT_SLUG": "school-c"}

    def test_unchanged_permission_outside_actor_grant_is_preserved_while_editor_changes_one_they_hold(self):
        target = Role.objects.create(tenant=self.tenant, name="Target", permissions=["finance.payment.reverse"])
        response = self.client.patch(
            f"/api/v1/tenancy/roles/{target.id}/",
            {"permissions": ["finance.payment.reverse", "finance.invoice.view"]}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data["permissions"]), {"finance.payment.reverse", "finance.invoice.view"})

    def test_adding_a_permission_the_actor_does_not_hold_is_rejected(self):
        target = Role.objects.create(tenant=self.tenant, name="Target", permissions=["finance.payment.reverse"])
        response = self.client.patch(
            f"/api/v1/tenancy/roles/{target.id}/",
            {"permissions": ["finance.payment.reverse", "finance.invoice.issue"]}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)
        target.refresh_from_db()
        self.assertEqual(target.permissions, ["finance.payment.reverse"])

    def test_removing_a_permission_the_actor_does_not_hold_is_rejected(self):
        target = Role.objects.create(tenant=self.tenant, name="Target", permissions=["finance.payment.reverse"])
        response = self.client.patch(
            f"/api/v1/tenancy/roles/{target.id}/", {"permissions": []}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)
        target.refresh_from_db()
        self.assertEqual(target.permissions, ["finance.payment.reverse"])

    def test_adding_a_permission_the_actor_holds_succeeds(self):
        target = Role.objects.create(tenant=self.tenant, name="Target", permissions=[])
        response = self.client.patch(
            f"/api/v1/tenancy/roles/{target.id}/", {"permissions": ["finance.invoice.view"]}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["permissions"], ["finance.invoice.view"])

    def test_removing_a_permission_the_actor_holds_succeeds(self):
        target = Role.objects.create(tenant=self.tenant, name="Target", permissions=["finance.invoice.view"])
        response = self.client.patch(
            f"/api/v1/tenancy/roles/{target.id}/", {"permissions": []}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["permissions"], [])

    def test_name_only_patch_never_touches_permissions_even_outside_actor_grant(self):
        target = Role.objects.create(tenant=self.tenant, name="Target", permissions=["finance.payment.reverse"])
        response = self.client.patch(
            f"/api/v1/tenancy/roles/{target.id}/", {"name": "Renamed Target"}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["name"], "Renamed Target")
        self.assertEqual(response.data["permissions"], ["finance.payment.reverse"])

    def test_last_administrator_protection_still_blocks_removing_membership_manage(self):
        # The editor's own role is the tenant's only administrator-granting
        # role; the editor holds tenancy.membership.manage themselves (so the
        # new added/removed authorization check would allow the removal),
        # but _ensure_not_removing_last_administrator must still reject it --
        # proving the two checks (authorization vs. last-admin safety) are
        # independent and both still run after this fix.
        self.editor_role.permissions = self.editor_role.permissions + ["tenancy.membership.manage"]
        self.editor_role.save(update_fields=["permissions"])
        response = self.client.patch(
            f"/api/v1/tenancy/roles/{self.editor_role.id}/",
            {"permissions": ["tenancy.role.manage", "tenancy.role.view", "finance.invoice.view"]},
            format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 400)
        self.editor_role.refresh_from_db()
        self.assertIn("tenancy.membership.manage", self.editor_role.permissions)

    def test_superuser_bypasses_grant_authorization_entirely(self):
        target = Role.objects.create(tenant=self.tenant, name="Target", permissions=["finance.payment.reverse"])
        superuser = User.objects.create_superuser(username="root-c", password="secret", email="root-c@example.com")
        # require_permission() always calls require_membership() first,
        # regardless of is_superuser -- only the permission-string check
        # itself is bypassed for superusers, so a membership (any role) is
        # still required to reach update_role() at all.
        no_permission_role = Role.objects.create(tenant=self.tenant, name="No Permissions", permissions=[])
        Membership.objects.create(tenant=self.tenant, user=superuser, role=no_permission_role)
        self.client.force_authenticate(superuser)
        response = self.client.patch(
            f"/api/v1/tenancy/roles/{target.id}/",
            {"permissions": ["finance.mpesa.configure", "finance.allocation.reverse"]}, format="json", **self.headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data["permissions"]), {"finance.mpesa.configure", "finance.allocation.reverse"})
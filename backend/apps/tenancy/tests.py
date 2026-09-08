from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.notifications.models import NotificationOutbox

from .context import active_tenant
from .models import AuditEvent, Campus, Membership, Role, Tenant, User
from .services import (
    ADMIN_GUARD_PERMISSION,
    accept_invite,
    activate_membership,
    create_role,
    deactivate_membership,
    delete_role,
    invite_user,
    require_membership,
    require_permission,
    require_same_tenant,
    update_membership,
    update_role,
)


class TenantIsolationTests(TestCase):
    def test_superuser_still_requires_active_tenant_membership(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        with self.assertRaises(ValidationError):
            require_permission(user=self.user, tenant=self.school_a, permission="students.view")
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role_a)
        require_permission(user=self.user, tenant=self.school_a, permission="students.view")
        Tenant.objects.filter(pk=self.school_a.pk).update(is_active=False)
        with self.assertRaises(ValidationError):
            require_permission(user=self.user, tenant=self.school_a, permission="students.view")

    def test_inactive_user_and_foreign_role_are_rejected(self):
        membership = Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role_a)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaises(ValidationError):
            require_membership(user=self.user, tenant=self.school_a)
        User.objects.filter(pk=self.user.pk).update(is_active=True)
        foreign_role = Role.objects.create(tenant=self.school_b, name="Foreign", permissions=["students.view"])
        Membership.objects.filter(pk=membership.pk).update(role=foreign_role)
        with self.assertRaises(ValidationError):
            require_permission(user=self.user, tenant=self.school_a, permission="students.view")

    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.user = User.objects.create_user(username="admin", password="secret")
        self.role_a = Role.objects.create(tenant=self.school_a, name="Administrator")
        self.campus_a = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")

    def test_tenant_queryset_only_returns_requested_tenant(self):
        Campus.objects.create(tenant=self.school_b, name="Main", code="MAIN")

        self.assertEqual(list(Campus.objects.for_tenant(self.school_a)), [self.campus_a])

    def test_membership_requires_active_membership_in_requested_tenant(self):
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role_a)

        self.assertEqual(require_membership(user=self.user, tenant=self.school_a).tenant, self.school_a)
        with self.assertRaises(ValidationError):
            require_membership(user=self.user, tenant=self.school_b)

    def test_inactive_membership_cannot_access_tenant(self):
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role_a, is_active=False)

        with self.assertRaises(ValidationError):
            require_membership(user=self.user, tenant=self.school_a)

    def test_role_permission_is_enforced(self):
        self.role_a.permissions = ["students.view"]
        self.role_a.save(update_fields=["permissions"])
        Membership.objects.create(tenant=self.school_a, user=self.user, role=self.role_a)

        require_permission(user=self.user, tenant=self.school_a, permission="students.view")
        with self.assertRaises(ValidationError):
            require_permission(user=self.user, tenant=self.school_a, permission="students.edit")

    def test_cross_tenant_objects_are_rejected(self):
        campus_b = Campus.objects.create(tenant=self.school_b, name="Main", code="MAIN")

        with self.assertRaisesMessage(ValidationError, "campus"):
            require_same_tenant(tenant=self.school_a, campus=campus_b)

    def test_missing_tenant_is_rejected(self):
        with self.assertRaises(ValueError):
            Campus.objects.for_tenant(None)

    def test_audit_events_are_tenant_scoped(self):
        event_a = AuditEvent.objects.create(
            tenant=self.school_a,
            actor=self.user,
            action="student.created",
            resource_type="student",
            resource_id="student-a",
        )
        AuditEvent.objects.create(
            tenant=self.school_b,
            action="student.created",
            resource_type="student",
            resource_id="student-b",
        )

        self.assertEqual(list(AuditEvent.objects.for_tenant(self.school_a)), [event_a])

    def test_activity_like_records_cannot_be_retrieved_without_tenant(self):
        with self.assertRaises(ValueError):
            AuditEvent.objects.for_tenant(None)

    def test_active_tenant_context_is_empty_outside_a_request(self):
        self.assertIsNone(active_tenant.get())


class TenancyAdminTestBase(TestCase):
    """Milestone 22.4: an actor holding every tenancy.* permission, used as
    the baseline for role/membership administration tests.
    """

    def setUp(self):
        self.school_a = Tenant.objects.create(name="School A", slug="school-a")
        self.school_b = Tenant.objects.create(name="School B", slug="school-b")
        self.admin_role = Role.objects.create(
            tenant=self.school_a, name="Administrator",
            permissions=["tenancy.membership.manage", "tenancy.role.manage", "tenancy.membership.view", "tenancy.role.view"],
        )
        self.admin_user = User.objects.create_user(username="admin-a", password="secret")
        self.admin_membership = Membership.objects.create(tenant=self.school_a, user=self.admin_user, role=self.admin_role)


class RoleServiceTests(TenancyAdminTestBase):
    def test_create_role_rejects_unknown_permission_code(self):
        with self.assertRaises(ValidationError):
            create_role(actor=self.admin_user, tenant=self.school_a, name="Bogus", permissions=["not.a.real.permission"])

    def test_create_role_rejects_ungrantable_permission(self):
        with self.assertRaises(ValidationError):
            create_role(actor=self.admin_user, tenant=self.school_a, name="Bursar", permissions=["finance.invoice.view"])

    def test_create_role_succeeds_with_grantable_permissions(self):
        role = create_role(actor=self.admin_user, tenant=self.school_a, name="Membership Viewer", permissions=["tenancy.membership.view"])
        self.assertEqual(role.permissions, ["tenancy.membership.view"])
        event = AuditEvent.objects.for_tenant(self.school_a).get(action="tenancy.role.created")
        self.assertEqual(event.actor, self.admin_user)

    def test_superuser_can_create_role_with_any_permission(self):
        self.admin_user.is_superuser = True
        self.admin_user.save(update_fields=["is_superuser"])
        role = create_role(actor=self.admin_user, tenant=self.school_a, name="Bursar", permissions=["finance.invoice.view"])
        self.assertIn("finance.invoice.view", role.permissions)

    def test_update_role_can_rename_and_change_permissions(self):
        role = create_role(actor=self.admin_user, tenant=self.school_a, name="Membership Viewer", permissions=["tenancy.membership.view"])
        updated = update_role(actor=self.admin_user, tenant=self.school_a, role=role, name="Renamed", permissions=["tenancy.role.view"])
        self.assertEqual(updated.name, "Renamed")
        self.assertEqual(updated.permissions, ["tenancy.role.view"])

    def test_delete_role_blocked_while_assigned(self):
        with self.assertRaises(ValidationError):
            delete_role(actor=self.admin_user, tenant=self.school_a, role=self.admin_role)

    def test_delete_role_succeeds_when_unassigned(self):
        role = create_role(actor=self.admin_user, tenant=self.school_a, name="Unused", permissions=[])
        delete_role(actor=self.admin_user, tenant=self.school_a, role=role)
        self.assertFalse(Role.objects.filter(pk=role.pk).exists())

    def test_duplicate_role_name_is_rejected(self):
        with self.assertRaises(ValidationError):
            create_role(actor=self.admin_user, tenant=self.school_a, name="Administrator", permissions=[])

    def test_actor_cannot_manage_a_role_in_a_different_tenant(self):
        foreign_role = Role.objects.create(tenant=self.school_b, name="Foreign", permissions=[])
        with self.assertRaises(ValidationError):
            update_role(actor=self.admin_user, tenant=self.school_a, role=foreign_role, name="Renamed")


class InviteAndAcceptTests(TenancyAdminTestBase):
    def setUp(self):
        super().setUp()
        self.member_role = Role.objects.create(tenant=self.school_a, name="Teacher", permissions=["students.view"])
        self.admin_role.permissions = self.admin_role.permissions + ["students.view"]
        self.admin_role.save(update_fields=["permissions"])

    def _invite_and_get_token(self, email="new.teacher@example.com"):
        membership = invite_user(actor=self.admin_user, tenant=self.school_a, email=email, role=self.member_role)
        outbox_entry = NotificationOutbox.objects.for_tenant(self.school_a).get(message_type="tenancy.user_invited", recipient=email)
        token = outbox_entry.context["invite_link"].split("token=")[1]
        return membership, token

    def test_invite_creates_an_unusable_password_user_and_membership(self):
        membership = invite_user(actor=self.admin_user, tenant=self.school_a, email="new.teacher@example.com", role=self.member_role)
        self.assertFalse(membership.user.has_usable_password())
        self.assertEqual(membership.user.email, "new.teacher@example.com")
        self.assertTrue(Membership.objects.filter(pk=membership.pk, tenant=self.school_a, role=self.member_role).exists())

    def test_invite_enqueues_a_notification(self):
        invite_user(actor=self.admin_user, tenant=self.school_a, email="new.teacher@example.com", role=self.member_role)
        outbox_entry = NotificationOutbox.objects.for_tenant(self.school_a).get(message_type="tenancy.user_invited")
        self.assertEqual(outbox_entry.recipient, "new.teacher@example.com")

    def test_invite_records_an_audit_event(self):
        membership = invite_user(actor=self.admin_user, tenant=self.school_a, email="new.teacher@example.com", role=self.member_role)
        event = AuditEvent.objects.for_tenant(self.school_a).get(action="tenancy.user.invited")
        self.assertEqual(event.resource_id, str(membership.id))

    def test_inviting_an_existing_member_is_rejected(self):
        invite_user(actor=self.admin_user, tenant=self.school_a, email="new.teacher@example.com", role=self.member_role)
        with self.assertRaises(ValidationError):
            invite_user(actor=self.admin_user, tenant=self.school_a, email="new.teacher@example.com", role=self.member_role)

    def test_invite_reuses_an_existing_user_by_email(self):
        existing = User.objects.create_user(username="existing", email="existing@example.com", password="whatever")
        membership = invite_user(actor=self.admin_user, tenant=self.school_a, email="EXISTING@example.com", role=self.member_role)
        self.assertEqual(membership.user_id, existing.id)

    def test_invite_rejects_a_role_the_actor_cannot_grant(self):
        finance_role = Role.objects.create(tenant=self.school_a, name="Bursar", permissions=["finance.invoice.view"])
        with self.assertRaises(ValidationError):
            invite_user(actor=self.admin_user, tenant=self.school_a, email="new.bursar@example.com", role=finance_role)

    def test_accept_invite_sets_a_usable_password(self):
        _, token = self._invite_and_get_token()
        user = accept_invite(token=token, password="a-strong-passw0rd!")
        user.refresh_from_db()
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password("a-strong-passw0rd!"))

    def test_accept_invite_is_single_use(self):
        _, token = self._invite_and_get_token()
        accept_invite(token=token, password="a-strong-passw0rd!")
        with self.assertRaises(ValidationError):
            accept_invite(token=token, password="another-passw0rd!")

    def test_accept_invite_rejects_a_tampered_token(self):
        with self.assertRaises(ValidationError):
            accept_invite(token="not-a-real-token", password="a-strong-passw0rd!")

    def test_accept_invite_enforces_password_validators(self):
        _, token = self._invite_and_get_token()
        with self.assertRaises(ValidationError):
            accept_invite(token=token, password="short")

    def test_accept_invite_records_a_tenant_scoped_audit_event(self):
        membership, token = self._invite_and_get_token()
        accept_invite(token=token, password="a-strong-passw0rd!")
        event = AuditEvent.objects.for_tenant(self.school_a).get(action="tenancy.user.invite_accepted")
        self.assertEqual(event.resource_id, str(membership.user_id))


class MembershipAdminServiceTests(TenancyAdminTestBase):
    def setUp(self):
        super().setUp()
        self.member_role = Role.objects.create(tenant=self.school_a, name="Teacher", permissions=["students.view"])
        self.admin_role.permissions = self.admin_role.permissions + ["students.view"]
        self.admin_role.save(update_fields=["permissions"])
        self.member_user = User.objects.create_user(username="member-a", password="secret")
        self.membership = Membership.objects.create(tenant=self.school_a, user=self.member_user, role=self.member_role)

    def test_deactivate_and_activate_membership(self):
        deactivate_membership(actor=self.admin_user, tenant=self.school_a, membership=self.membership)
        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_active)
        activate_membership(actor=self.admin_user, tenant=self.school_a, membership=self.membership)
        self.membership.refresh_from_db()
        self.assertTrue(self.membership.is_active)

    def test_deactivate_records_an_audit_event(self):
        deactivate_membership(actor=self.admin_user, tenant=self.school_a, membership=self.membership)
        self.assertTrue(AuditEvent.objects.for_tenant(self.school_a).filter(action="tenancy.membership.deactivated").exists())

    def test_update_membership_changes_role(self):
        other_role = create_role(actor=self.admin_user, tenant=self.school_a, name="Registrar", permissions=["students.view"])
        updated = update_membership(actor=self.admin_user, tenant=self.school_a, membership=self.membership, role=other_role)
        self.assertEqual(updated.role_id, other_role.id)

    def test_update_membership_can_clear_campus(self):
        campus = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.membership.campus = campus
        self.membership.save(update_fields=["campus"])
        updated = update_membership(actor=self.admin_user, tenant=self.school_a, membership=self.membership, campus=None)
        self.assertIsNone(updated.campus_id)

    def test_update_membership_omitted_campus_is_unchanged(self):
        campus = Campus.objects.create(tenant=self.school_a, name="Main", code="MAIN")
        self.membership.campus = campus
        self.membership.save(update_fields=["campus"])
        update_membership(actor=self.admin_user, tenant=self.school_a, membership=self.membership, role=self.member_role)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.campus_id, campus.id)

    def test_update_membership_rejects_a_role_the_actor_cannot_grant(self):
        finance_role = Role.objects.create(tenant=self.school_a, name="Bursar", permissions=["finance.invoice.view"])
        with self.assertRaises(ValidationError):
            update_membership(actor=self.admin_user, tenant=self.school_a, membership=self.membership, role=finance_role)

    def test_cross_tenant_role_assignment_is_rejected(self):
        foreign_role = Role.objects.create(tenant=self.school_b, name="Foreign", permissions=[])
        with self.assertRaises(ValidationError):
            update_membership(actor=self.admin_user, tenant=self.school_a, membership=self.membership, role=foreign_role)

    def test_actor_cannot_deactivate_a_membership_in_a_different_tenant(self):
        foreign_role = Role.objects.create(tenant=self.school_b, name="Foreign", permissions=[])
        foreign_user = User.objects.create_user(username="foreign", password="secret")
        foreign_membership = Membership.objects.create(tenant=self.school_b, user=foreign_user, role=foreign_role)
        with self.assertRaises(ValidationError):
            deactivate_membership(actor=self.admin_user, tenant=self.school_a, membership=foreign_membership)


class LastAdministratorProtectionTests(TenancyAdminTestBase):
    def test_deactivating_the_only_administrator_is_blocked(self):
        with self.assertRaises(ValidationError):
            deactivate_membership(actor=self.admin_user, tenant=self.school_a, membership=self.admin_membership)

    def test_deactivating_one_of_two_administrators_is_allowed(self):
        second_admin_user = User.objects.create_user(username="admin-a-2", password="secret")
        Membership.objects.create(tenant=self.school_a, user=second_admin_user, role=self.admin_role)
        deactivate_membership(actor=self.admin_user, tenant=self.school_a, membership=self.admin_membership)
        self.admin_membership.refresh_from_db()
        self.assertFalse(self.admin_membership.is_active)

    def test_reassigning_the_last_administrator_away_is_blocked(self):
        viewer_role = create_role(actor=self.admin_user, tenant=self.school_a, name="Viewer", permissions=["tenancy.membership.view"])
        with self.assertRaises(ValidationError):
            update_membership(actor=self.admin_user, tenant=self.school_a, membership=self.admin_membership, role=viewer_role)

    def test_stripping_the_admin_permission_from_the_only_admin_role_is_blocked(self):
        with self.assertRaises(ValidationError):
            update_role(actor=self.admin_user, tenant=self.school_a, role=self.admin_role, permissions=["tenancy.role.manage"])

    def test_stripping_the_admin_permission_is_allowed_when_another_admin_role_exists(self):
        other_admin_role = create_role(
            actor=self.admin_user, tenant=self.school_a, name="Co-Administrator",
            permissions=["tenancy.membership.manage", "tenancy.role.manage"],
        )
        second_admin_user = User.objects.create_user(username="admin-a-2", password="secret")
        Membership.objects.create(tenant=self.school_a, user=second_admin_user, role=other_admin_role)
        update_role(actor=self.admin_user, tenant=self.school_a, role=self.admin_role, permissions=["tenancy.role.manage"])
        self.admin_role.refresh_from_db()
        self.assertNotIn(ADMIN_GUARD_PERMISSION, self.admin_role.permissions)

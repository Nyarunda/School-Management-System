from django.core.exceptions import ValidationError
from django.test import TestCase

from .context import active_tenant
from .models import AuditEvent, Campus, Membership, Role, Tenant, User
from .services import require_membership, require_permission, require_same_tenant


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

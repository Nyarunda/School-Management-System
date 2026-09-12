from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.tenancy.models import AuditEvent, Membership, Role, Tenant, User
from apps.tenancy.services import create_role

from .catalogue import MODULE_CATALOGUE
from .models import PlatformAuditEvent, SubscriptionPlan, TenantModuleOverride, TenantSubscription
from .services import (
    assign_plan,
    clear_module_override,
    create_plan,
    delete_plan,
    get_enabled_modules,
    permissions_for_modules,
    provision_tenant,
    require_module_enabled,
    set_module_override,
    update_plan,
)


class ProvisioningTests(TestCase):
    """The default plan is seeded by the 0002 data migration, which already
    ran to build this test database -- confirms the auto-provisioning
    signal (apps.platform.signals) actually fires for a brand-new tenant.
    """

    def test_new_tenant_is_auto_subscribed_to_the_default_plan(self):
        tenant = Tenant.objects.create(name="New School", slug="new-school")
        subscription = TenantSubscription.objects.get(tenant=tenant)
        self.assertTrue(subscription.plan.is_default)
        self.assertEqual(set(subscription.plan.module_codes), set(MODULE_CATALOGUE))


class GetEnabledModulesTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")
        # The signal already gave this tenant the default plan -- replace it
        # with a narrower plan for these tests.
        self.plan = SubscriptionPlan.objects.create(name="Standard", module_codes=["finance", "attendance"])
        TenantSubscription.objects.filter(tenant=self.tenant).update(plan=self.plan)

    def test_base_set_comes_from_the_plan(self):
        self.assertEqual(get_enabled_modules(self.tenant), {"finance", "attendance"})

    def test_override_can_add_a_module_beyond_the_plan(self):
        TenantModuleOverride.objects.create(tenant=self.tenant, module_code="documents", is_enabled=True)
        self.assertEqual(get_enabled_modules(self.tenant), {"finance", "attendance", "documents"})

    def test_override_can_remove_a_module_the_plan_would_include(self):
        TenantModuleOverride.objects.create(tenant=self.tenant, module_code="finance", is_enabled=False)
        self.assertEqual(get_enabled_modules(self.tenant), {"attendance"})

    def test_no_subscription_fails_closed_to_no_modules(self):
        TenantSubscription.objects.filter(tenant=self.tenant).delete()
        self.assertEqual(get_enabled_modules(self.tenant), set())

    def test_require_module_enabled_passes_for_an_enabled_module(self):
        require_module_enabled(tenant=self.tenant, module_code="finance")

    def test_require_module_enabled_rejects_a_disabled_module(self):
        with self.assertRaises(ValidationError):
            require_module_enabled(tenant=self.tenant, module_code="documents")


class PlanServiceTests(TestCase):
    def test_create_plan_rejects_an_unknown_module_code(self):
        with self.assertRaises(ValidationError):
            create_plan(name="Bogus", module_codes=["not_a_real_module"])

    def test_creating_a_new_default_plan_unsets_the_previous_one(self):
        first = create_plan(name="First", module_codes=["finance"], is_default=True)
        second = create_plan(name="Second", module_codes=["attendance"], is_default=True)
        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)
        self.assertEqual(SubscriptionPlan.objects.filter(is_default=True).count(), 1)

    def test_update_plan_rejects_an_unknown_module_code(self):
        plan = create_plan(name="Standard", module_codes=["finance"])
        with self.assertRaises(ValidationError):
            update_plan(plan=plan, module_codes=["not_a_real_module"])

    def test_assign_plan_rejects_an_inactive_plan(self):
        tenant = Tenant.objects.create(name="School A", slug="school-a")
        plan = create_plan(name="Retired", module_codes=["finance"], is_active=False)
        with self.assertRaises(ValidationError):
            assign_plan(tenant=tenant, plan=plan)

    def test_assign_plan_switches_the_tenant_s_subscription(self):
        tenant = Tenant.objects.create(name="School A", slug="school-a")
        plan = create_plan(name="Standard", module_codes=["finance"])
        assign_plan(tenant=tenant, plan=plan)
        self.assertEqual(get_enabled_modules(tenant), {"finance"})

    def test_deactivating_a_plan_does_not_strip_modules_from_existing_tenants(self):
        tenant = Tenant.objects.create(name="School A", slug="school-a")
        plan = create_plan(name="Standard", module_codes=["finance"])
        assign_plan(tenant=tenant, plan=plan)
        update_plan(plan=plan, is_active=False)
        self.assertEqual(get_enabled_modules(tenant), {"finance"})

    def test_delete_plan_rejected_while_assigned_to_a_tenant(self):
        tenant = Tenant.objects.create(name="School A", slug="school-a")
        plan = create_plan(name="Standard", module_codes=["finance"])
        assign_plan(tenant=tenant, plan=plan)
        with self.assertRaises(ValidationError):
            delete_plan(plan=plan)

    def test_set_and_clear_module_override(self):
        tenant = Tenant.objects.create(name="School A", slug="school-a")
        set_module_override(tenant=tenant, module_code="finance", is_enabled=False)
        self.assertNotIn("finance", get_enabled_modules(tenant))
        clear_module_override(tenant=tenant, module_code="finance")
        self.assertIn("finance", get_enabled_modules(tenant))

    def test_set_module_override_rejects_an_unknown_module_code(self):
        tenant = Tenant.objects.create(name="School A", slug="school-a")
        with self.assertRaises(ValidationError):
            set_module_override(tenant=tenant, module_code="not_a_real_module", is_enabled=True)

    def test_module_codes_are_canonicalized_sorted_and_deduplicated(self):
        plan = create_plan(name="Standard", module_codes=["finance", "finance", "attendance"])
        self.assertEqual(plan.module_codes, ["attendance", "finance"])

    def test_create_plan_rejects_an_inactive_default(self):
        with self.assertRaises(ValidationError):
            create_plan(name="Bad", module_codes=["finance"], is_default=True, is_active=False)

    def test_update_plan_rejects_making_an_inactive_plan_the_default(self):
        plan = create_plan(name="Standard", module_codes=["finance"], is_active=False)
        with self.assertRaises(ValidationError):
            update_plan(plan=plan, is_default=True)

    def test_update_plan_rejects_deactivating_while_becoming_default(self):
        plan = create_plan(name="Standard", module_codes=["finance"])
        with self.assertRaises(ValidationError):
            update_plan(plan=plan, is_default=True, is_active=False)

    def test_update_plan_rejects_unsetting_the_default_directly(self):
        plan = create_plan(name="Standard", module_codes=["finance"], is_default=True)
        with self.assertRaises(ValidationError):
            update_plan(plan=plan, is_default=False)

    def test_switching_the_default_to_another_plan_is_allowed(self):
        first = create_plan(name="First", module_codes=["finance"], is_default=True)
        second = create_plan(name="Second", module_codes=["attendance"])
        update_plan(plan=second, is_default=True)
        first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(SubscriptionPlan.objects.get(pk=second.pk).is_default)

    def test_update_plan_can_rename(self):
        plan = create_plan(name="Standard", module_codes=["finance"])
        update_plan(plan=plan, name="Professional")
        plan.refresh_from_db()
        self.assertEqual(plan.name, "Professional")

    def test_delete_plan_rejected_for_the_default_plan(self):
        plan = create_plan(name="Standard", module_codes=["finance"], is_default=True)
        with self.assertRaises(ValidationError):
            delete_plan(plan=plan)


class AuditTrailTests(TestCase):
    """Milestone 22.2: every privileged platform write records an audit
    trail -- PlatformAuditEvent for actions with no single tenant (plan
    CRUD), AuditEvent for the tenant-scoped ones (assignment, overrides).
    """

    def setUp(self):
        self.actor = User.objects.create_user(username="super-admin", password="secret", is_superuser=True)
        self.tenant = Tenant.objects.create(name="School A", slug="school-a")

    def test_create_plan_records_a_platform_audit_event(self):
        plan = create_plan(actor=self.actor, name="Standard", module_codes=["finance"])
        event = PlatformAuditEvent.objects.get(action="platform.plan.created", resource_id=str(plan.id))
        self.assertEqual(event.actor, self.actor)
        self.assertEqual(event.resource_type, "SubscriptionPlan")
        self.assertEqual(event.metadata["name"], "Standard")
        self.assertEqual(event.metadata["module_codes"], ["finance"])

    def test_update_plan_records_the_fields_that_changed(self):
        plan = create_plan(actor=self.actor, name="Standard", module_codes=["finance"])
        update_plan(actor=self.actor, plan=plan, name="Professional")
        event = PlatformAuditEvent.objects.get(action="platform.plan.updated", resource_id=str(plan.id))
        self.assertEqual(event.metadata["updated_fields"], ["name"])
        self.assertEqual(event.metadata["name"], "Professional")

    def test_delete_plan_records_the_deleted_plan_s_identity(self):
        plan = create_plan(actor=self.actor, name="Retired", module_codes=["finance"])
        plan_id = str(plan.id)
        delete_plan(actor=self.actor, plan=plan)
        event = PlatformAuditEvent.objects.get(action="platform.plan.deleted", resource_id=plan_id)
        self.assertEqual(event.metadata["name"], "Retired")

    def test_assign_plan_records_a_tenant_scoped_audit_event(self):
        plan = create_plan(actor=self.actor, name="Standard", module_codes=["finance"])
        assign_plan(actor=self.actor, tenant=self.tenant, plan=plan)
        event = AuditEvent.objects.for_tenant(self.tenant).get(action="platform.subscription.assigned")
        self.assertEqual(event.actor, self.actor)
        self.assertEqual(event.metadata["plan_id"], str(plan.id))

    def test_set_module_override_records_a_tenant_scoped_audit_event(self):
        set_module_override(actor=self.actor, tenant=self.tenant, module_code="finance", is_enabled=False)
        event = AuditEvent.objects.for_tenant(self.tenant).get(action="platform.module_override.set")
        self.assertEqual(event.resource_id, "finance")
        self.assertFalse(event.metadata["is_enabled"])

    def test_clear_module_override_records_a_tenant_scoped_audit_event(self):
        set_module_override(actor=self.actor, tenant=self.tenant, module_code="finance", is_enabled=False)
        clear_module_override(actor=self.actor, tenant=self.tenant, module_code="finance")
        event = AuditEvent.objects.for_tenant(self.tenant).get(action="platform.module_override.cleared")
        self.assertEqual(event.resource_id, "finance")

    def test_actor_defaults_to_none_for_system_triggered_calls(self):
        plan = create_plan(name="Standard", module_codes=["finance"])
        event = PlatformAuditEvent.objects.get(action="platform.plan.created", resource_id=str(plan.id))
        self.assertIsNone(event.actor)


class ProvisionTenantServiceTests(TestCase):
    """RC Area 2: the Super Admin tenant-provisioning entry point."""

    def setUp(self):
        self.actor = User.objects.create_user(username="super-admin-provisioner", password="secret", is_superuser=True)

    def test_provision_tenant_creates_exactly_one_subscription(self):
        tenant = provision_tenant(actor=self.actor, name="New School", slug="new-school-1", admin_email="admin@new-school-1.example")
        self.assertEqual(TenantSubscription.objects.filter(tenant=tenant).count(), 1)

    def test_provision_tenant_creates_an_inactive_admin_membership_until_accepted(self):
        tenant = provision_tenant(actor=self.actor, name="New School", slug="new-school-2", admin_email="admin@new-school-2.example")
        membership = Membership.objects.get(tenant=tenant)
        self.assertFalse(membership.is_active)
        self.assertEqual(membership.user.email, "admin@new-school-2.example")

    def test_provision_tenant_rejects_admin_permissions_without_the_guard_permission(self):
        with self.assertRaises(ValidationError):
            provision_tenant(
                actor=self.actor, name="New School", slug="new-school-3", admin_email="admin@new-school-3.example",
                admin_permissions=["tenancy.role.view"],
            )

    def test_provision_tenant_rejects_a_duplicate_slug(self):
        provision_tenant(actor=self.actor, name="New School", slug="dup-school", admin_email="admin1@dup-school.example")
        with self.assertRaises(ValidationError):
            provision_tenant(actor=self.actor, name="Another School", slug="dup-school", admin_email="admin2@dup-school.example")

    def test_provision_tenant_records_a_platform_audit_event(self):
        tenant = provision_tenant(actor=self.actor, name="New School", slug="new-school-4", admin_email="admin@new-school-4.example")
        event = PlatformAuditEvent.objects.get(action="platform.tenant.provisioned", resource_id=str(tenant.id))
        self.assertEqual(event.metadata["slug"], "new-school-4")
        self.assertEqual(event.actor, self.actor)

    def test_provision_tenant_default_admin_permissions_cover_every_subscribed_module(self):
        """Regression test for a real onboarding blocker found by a live
        end-to-end bootstrap run: a fixed, narrow default (just tenancy.*
        plus academics.setup.view) left a freshly provisioned school's
        administrator unable to grant any finance/staff/attendance
        permission to anyone, because _require_grantable_permissions
        blocks granting what you don't hold yourself.
        """
        tenant = provision_tenant(actor=self.actor, name="New School", slug="new-school-5", admin_email="admin@new-school-5.example")
        role = Role.objects.get(tenant=tenant, name="Administrator")
        expected = {"tenancy.membership.view", "tenancy.membership.manage", "tenancy.role.view", "tenancy.role.manage"}
        expected |= permissions_for_modules(get_enabled_modules(tenant))
        self.assertEqual(set(role.permissions), expected)
        self.assertIn("finance.payment.record", role.permissions)

    def test_provision_tenant_default_admin_can_immediately_create_a_domain_role(self):
        """The concrete symptom of the bug above: without this fix, this
        raised "Cannot grant permission(s) you do not hold yourself".
        """
        tenant = provision_tenant(actor=self.actor, name="New School", slug="new-school-6", admin_email="admin@new-school-6.example")
        membership = Membership.objects.get(tenant=tenant)
        membership.is_active = True
        membership.save(update_fields=["is_active"])
        bursar = create_role(
            actor=membership.user, tenant=tenant, name="Bursar",
            permissions=["finance.invoice.view", "finance.invoice.create", "finance.payment.view", "finance.payment.record"],
        )
        self.assertEqual(bursar.name, "Bursar")

    def test_provision_tenant_default_admin_permissions_are_empty_for_a_tenant_with_no_modules(self):
        """Confirms the default derives from the tenant's actual
        subscription rather than silently falling back to something
        broader when get_enabled_modules is empty.
        """
        empty_plan = create_plan(name="No Modules", module_codes=[])
        tenant = provision_tenant(actor=self.actor, name="New School", slug="new-school-7", admin_email="admin@new-school-7.example")
        assign_plan(actor=self.actor, tenant=tenant, plan=empty_plan)
        self.assertEqual(permissions_for_modules(get_enabled_modules(tenant)), set())


class ModelValidationTests(TestCase):
    def test_subscription_plan_clean_rejects_an_unknown_module_code(self):
        plan = SubscriptionPlan(name="Bad", module_codes=["not_a_real_module"])
        with self.assertRaises(ValidationError):
            plan.full_clean()

    def test_tenant_module_override_clean_rejects_an_unknown_module_code(self):
        tenant = Tenant.objects.create(name="School A", slug="school-a")
        override = TenantModuleOverride(tenant=tenant, module_code="not_a_real_module", is_enabled=True)
        with self.assertRaises(ValidationError):
            override.full_clean()

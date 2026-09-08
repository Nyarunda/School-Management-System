from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.tenancy.models import Tenant

from .catalogue import MODULE_CATALOGUE
from .models import SubscriptionPlan, TenantModuleOverride, TenantSubscription
from .services import (
    assign_plan,
    clear_module_override,
    create_plan,
    delete_plan,
    get_enabled_modules,
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

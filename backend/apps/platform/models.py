import uuid

from django.db import models

from apps.tenancy.models import Tenant

from .catalogue import MODULE_CATALOGUE


class SubscriptionPlan(models.Model):
    """A named bundle of module codes a Super Admin can assign to a tenant.
    Not a TenantOwnedModel -- this is platform-wide catalog data managed by
    Super Admins, not a tenant's own business data reached via Membership.

    is_active=False means unavailable for *new* assignment (can't become a
    tenant's plan, can't become the default) -- it never retroactively
    strips modules from tenants already on it. Suspending an existing
    tenant's access is a TenantModuleOverride decision, not a plan-level one.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    module_codes = models.JSONField(default=list)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # DB-level, not just service-level: a race between two
            # concurrent "make this the default plan" requests must never
            # leave two rows both flagged default.
            models.UniqueConstraint(
                fields=["is_default"], condition=models.Q(is_default=True), name="unique_default_subscription_plan",
            )
        ]

    def __str__(self):
        return self.name

    def clean(self):
        from django.core.exceptions import ValidationError

        unknown = set(self.module_codes) - set(MODULE_CATALOGUE)
        if unknown:
            raise ValidationError(f"Unknown module code(s): {', '.join(sorted(unknown))}")


class TenantSubscription(models.Model):
    """Which plan a tenant is currently on. One row per tenant -- assigning
    a new plan updates this row rather than creating a history of them
    (subscription history, if ever needed, is a Milestone 22 audit-trail
    concern, not this one).
    """

    tenant = models.OneToOneField(Tenant, on_delete=models.PROTECT, related_name="subscription")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    updated_at = models.DateTimeField(auto_now=True)


class TenantModuleOverride(models.Model):
    """An explicit exception to a tenant's plan for one module: is_enabled
    True force-adds a module beyond the plan, False force-removes one the
    plan would otherwise include.
    """

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="module_overrides")
    module_code = models.CharField(max_length=40)
    is_enabled = models.BooleanField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "module_code"], name="unique_override_per_tenant_module")
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.module_code not in MODULE_CATALOGUE:
            raise ValidationError(f"Unknown module code: {self.module_code}")

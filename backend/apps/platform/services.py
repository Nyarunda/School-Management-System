from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .catalogue import MODULE_CATALOGUE
from .models import SubscriptionPlan, TenantModuleOverride, TenantSubscription


def get_enabled_modules(tenant):
    """Fail closed: no TenantSubscription means no modules, never a silent
    fallback to full access. Provisioning (signals.provision_default_subscription
    and the initial data migration) is the safety net that keeps this from
    actually happening for a real tenant -- runtime evaluation doesn't lean
    on that guarantee.
    """
    try:
        subscription = TenantSubscription.objects.select_related("plan").get(tenant=tenant)
    except TenantSubscription.DoesNotExist:
        return set()
    enabled = set(subscription.plan.module_codes)
    for override in TenantModuleOverride.objects.filter(tenant=tenant):
        if override.is_enabled:
            enabled.add(override.module_code)
        else:
            enabled.discard(override.module_code)
    return enabled


def require_module_enabled(*, tenant, module_code):
    """Same shape/exception type as apps.tenancy.services.require_permission
    so every existing `except ValidationError: raise PermissionDenied`
    wrapping in each domain's resolve_<domain>_tenant helper needs no
    changes to also cover this check.
    """
    if module_code not in get_enabled_modules(tenant):
        raise ValidationError(f"Module not enabled for this tenant: {module_code}")


def _validate_module_codes(module_codes):
    """Canonicalizes to a sorted, deduplicated list -- storage stays
    deterministic (API responses, tests, and any future caching/auditing
    all see the same shape regardless of what order/duplication a caller
    submitted).
    """
    canonical = sorted(set(module_codes))
    unknown = set(canonical) - set(MODULE_CATALOGUE)
    if unknown:
        raise ValidationError(f"Unknown module code(s): {', '.join(sorted(unknown))}")
    return canonical


def create_plan(*, name, module_codes, is_default=False, is_active=True):
    canonical_codes = _validate_module_codes(module_codes)
    if is_default and not is_active:
        raise ValidationError("An inactive plan cannot be the default plan")
    try:
        with transaction.atomic():
            if is_default:
                SubscriptionPlan.objects.filter(is_default=True).update(is_default=False)
            return SubscriptionPlan.objects.create(
                name=name, module_codes=canonical_codes, is_default=is_default, is_active=is_active,
            )
    except IntegrityError as error:
        if is_default:
            raise ValidationError("Another request changed the default plan at the same time; try again") from error
        raise


def update_plan(*, plan, name=None, module_codes=None, is_active=None, is_default=None):
    """Two invariants beyond simple field updates, both there so
    services.get_enabled_modules and signals.provision_default_subscription
    can keep assuming "there is always exactly one active default plan"
    rather than defending against a momentarily-missing one:

    - An inactive plan can never be (or become) the default.
    - The current default can't be unset directly -- switching defaults
      means making a *different* active plan the default, which atomically
      unsets this one in the same transaction (see create_plan/this
      function's is_default=True branch).
    """
    resulting_is_active = plan.is_active if is_active is None else is_active
    resulting_is_default = plan.is_default if is_default is None else is_default
    if resulting_is_default and not resulting_is_active:
        raise ValidationError("An inactive plan cannot be the default plan")
    if plan.is_default and is_default is False:
        raise ValidationError("Cannot unset the default plan directly -- set a different active plan as default instead")

    try:
        with transaction.atomic():
            if name is not None:
                plan.name = name
            if module_codes is not None:
                plan.module_codes = _validate_module_codes(module_codes)
            if is_default:
                SubscriptionPlan.objects.filter(is_default=True).exclude(pk=plan.pk).update(is_default=False)
            if is_default is not None:
                plan.is_default = is_default
            if is_active is not None:
                plan.is_active = is_active
            plan.save()
            return plan
    except IntegrityError as error:
        if is_default:
            raise ValidationError("Another request changed the default plan at the same time; try again") from error
        raise


def delete_plan(*, plan):
    if plan.is_default:
        raise ValidationError("Cannot delete the default plan -- make a different plan the default first")
    if TenantSubscription.objects.filter(plan=plan).exists():
        raise ValidationError("Cannot delete a plan that is currently assigned to a tenant")
    plan.delete()


def assign_plan(*, tenant, plan):
    """is_active=False blocks new assignment -- it never retroactively
    strips a tenant already on the plan (see SubscriptionPlan's docstring).
    """
    if not plan.is_active:
        raise ValidationError("Cannot assign an inactive plan")
    subscription, _ = TenantSubscription.objects.update_or_create(tenant=tenant, defaults={"plan": plan})
    return subscription


def set_module_override(*, tenant, module_code, is_enabled):
    _validate_module_codes([module_code])
    override, _ = TenantModuleOverride.objects.update_or_create(
        tenant=tenant, module_code=module_code, defaults={"is_enabled": is_enabled},
    )
    return override


def clear_module_override(*, tenant, module_code):
    TenantModuleOverride.objects.filter(tenant=tenant, module_code=module_code).delete()

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.tenancy.models import AuditEvent, Role, Tenant
from apps.tenancy.permissions_catalogue import validate_permission_codes
from apps.tenancy.services import ADMIN_GUARD_PERMISSION, invite_user

from .catalogue import MODULE_CATALOGUE, MODULE_PERMISSION_PREFIXES
from .models import PlatformAuditEvent, SubscriptionPlan, TenantModuleOverride, TenantSubscription


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


def permissions_for_modules(module_codes):
    """Every apps.tenancy.permissions_catalogue.PERMISSION_CATALOGUE code
    whose prefix belongs to one of the given modules -- see
    MODULE_PERMISSION_PREFIXES. Used to compute what a tenant's first
    administrator can delegate (provision_tenant), not to enforce
    module-gating itself (require_module_enabled already does that).
    """
    from apps.tenancy.permissions_catalogue import PERMISSION_CATALOGUE

    prefixes = tuple(
        prefix for module_code in module_codes for prefix in MODULE_PERMISSION_PREFIXES.get(module_code, ())
    )
    return {code for code in PERMISSION_CATALOGUE if code.startswith(prefixes)}


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


def create_plan(*, actor=None, name, module_codes, is_default=False, is_active=True):
    canonical_codes = _validate_module_codes(module_codes)
    if is_default and not is_active:
        raise ValidationError("An inactive plan cannot be the default plan")
    try:
        with transaction.atomic():
            if is_default:
                SubscriptionPlan.objects.filter(is_default=True).update(is_default=False)
            plan = SubscriptionPlan.objects.create(
                name=name, module_codes=canonical_codes, is_default=is_default, is_active=is_active,
            )
            PlatformAuditEvent.objects.create(
                actor=actor, action="platform.plan.created", resource_type="SubscriptionPlan",
                resource_id=str(plan.id),
                metadata={
                    "name": plan.name, "module_codes": plan.module_codes,
                    "is_default": plan.is_default, "is_active": plan.is_active,
                },
            )
            return plan
    except IntegrityError as error:
        if is_default:
            raise ValidationError("Another request changed the default plan at the same time; try again") from error
        raise


def update_plan(*, actor=None, plan, name=None, module_codes=None, is_active=None, is_default=None):
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

    updated_fields = [
        field for field, value in
        (("name", name), ("module_codes", module_codes), ("is_active", is_active), ("is_default", is_default))
        if value is not None
    ]
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
            PlatformAuditEvent.objects.create(
                actor=actor, action="platform.plan.updated", resource_type="SubscriptionPlan",
                resource_id=str(plan.id),
                metadata={
                    "updated_fields": updated_fields, "name": plan.name, "module_codes": plan.module_codes,
                    "is_default": plan.is_default, "is_active": plan.is_active,
                },
            )
            return plan
    except IntegrityError as error:
        if is_default:
            raise ValidationError("Another request changed the default plan at the same time; try again") from error
        raise


def delete_plan(*, actor=None, plan):
    if plan.is_default:
        raise ValidationError("Cannot delete the default plan -- make a different plan the default first")
    if TenantSubscription.objects.filter(plan=plan).exists():
        raise ValidationError("Cannot delete a plan that is currently assigned to a tenant")
    with transaction.atomic():
        plan_id, name, module_codes = str(plan.id), plan.name, plan.module_codes
        plan.delete()
        PlatformAuditEvent.objects.create(
            actor=actor, action="platform.plan.deleted", resource_type="SubscriptionPlan", resource_id=plan_id,
            metadata={"name": name, "module_codes": module_codes},
        )


def assign_plan(*, actor=None, tenant, plan):
    """is_active=False blocks new assignment -- it never retroactively
    strips a tenant already on the plan (see SubscriptionPlan's docstring).
    """
    if not plan.is_active:
        raise ValidationError("Cannot assign an inactive plan")
    with transaction.atomic():
        subscription, _ = TenantSubscription.objects.update_or_create(tenant=tenant, defaults={"plan": plan})
        AuditEvent.objects.create(
            tenant=tenant, actor=actor, action="platform.subscription.assigned",
            resource_type="TenantSubscription", resource_id=str(subscription.pk),
            metadata={"plan_id": str(plan.id), "plan_name": plan.name},
        )
        return subscription


def set_module_override(*, actor=None, tenant, module_code, is_enabled):
    _validate_module_codes([module_code])
    with transaction.atomic():
        override, _ = TenantModuleOverride.objects.update_or_create(
            tenant=tenant, module_code=module_code, defaults={"is_enabled": is_enabled},
        )
        AuditEvent.objects.create(
            tenant=tenant, actor=actor, action="platform.module_override.set",
            resource_type="TenantModuleOverride", resource_id=module_code,
            metadata={"is_enabled": is_enabled},
        )
        return override


def clear_module_override(*, actor=None, tenant, module_code):
    with transaction.atomic():
        TenantModuleOverride.objects.filter(tenant=tenant, module_code=module_code).delete()
        AuditEvent.objects.create(
            tenant=tenant, actor=actor, action="platform.module_override.cleared",
            resource_type="TenantModuleOverride", resource_id=module_code, metadata={},
        )


def provision_tenant(*, actor, name, slug, admin_email, admin_role_name="Administrator", admin_permissions=None):
    """The one Super Admin entry point for bringing a new tenant into
    existence with its first administrator -- everything else (schools
    onboarding staff, students, etc.) happens from inside that tenant once
    this has run. Deliberately reuses apps.tenancy.services.invite_user for
    the admin's User/Membership rather than creating them directly, so the
    initial admin goes through the exact same consent-gated invite/accept
    flow as anyone else invited later (their membership starts inactive
    until they accept).

    When admin_permissions isn't given, the default is every permission
    belonging to a module the tenant is actually subscribed to (plus the
    always-ungated tenancy.* administration permissions) -- not a fixed
    handful. A School Administrator who can only manage tenancy/academics
    setup can't grant finance/staff/attendance permissions to anyone
    (_require_grantable_permissions blocks granting what you don't hold
    yourself), so a narrower fixed default would leave a freshly
    provisioned school unable to create its own Bursar/Teacher/HR roles.
    """
    with transaction.atomic():
        try:
            # Tenant.objects.create's post_save signal
            # (apps.platform.signals.provision_default_subscription)
            # assigns the default TenantSubscription -- not duplicated here,
            # and read below (get_enabled_modules) to size the default
            # admin permission set to what this tenant actually subscribes to.
            tenant = Tenant.objects.create(name=name, slug=slug)
        except IntegrityError as error:
            raise ValidationError("A tenant with this slug already exists") from error

        if admin_permissions is None:
            from apps.tenancy.permissions_catalogue import PERMISSION_CATALOGUE

            tenancy_permissions = {code for code in PERMISSION_CATALOGUE if code.startswith("tenancy.")}
            admin_permissions = sorted(tenancy_permissions | permissions_for_modules(get_enabled_modules(tenant)))
        canonical = validate_permission_codes(admin_permissions)
        if ADMIN_GUARD_PERMISSION not in canonical:
            raise ValidationError(f"The initial admin role must include {ADMIN_GUARD_PERMISSION}")

        role = Role.objects.create(tenant=tenant, name=admin_role_name, permissions=canonical)
        membership = invite_user(actor=actor, tenant=tenant, email=admin_email, role=role)
        PlatformAuditEvent.objects.create(
            actor=actor, action="platform.tenant.provisioned", resource_type="Tenant", resource_id=str(tenant.id),
            metadata={"slug": tenant.slug, "admin_email": membership.user.email},
        )
    return tenant

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.tenancy.models import Tenant

from .models import SubscriptionPlan, TenantSubscription


@receiver(post_save, sender=Tenant)
def provision_default_subscription(sender, instance, created, **kwargs):
    """Every Tenant row must end up with a subscription so
    services.get_enabled_modules never has to guess between "genuinely no
    access" and "nobody's configured this tenant yet." A signal, not a
    create_tenant() service call, because this codebase has no single
    tenant-creation entry point today -- Tenant rows are created directly
    via the ORM (tests, and eventually whatever onboarding path ships
    later) -- so this is the one place guaranteed to run regardless of how
    a Tenant comes into existence. A no-op if there's no default plan yet
    (only possible before the seeding data migration runs); the data
    migration separately backfills every pre-existing tenant.
    """
    if not created:
        return
    if TenantSubscription.objects.filter(tenant=instance).exists():
        return
    default_plan = SubscriptionPlan.objects.filter(is_default=True).first()
    if default_plan is None:
        return
    TenantSubscription.objects.create(tenant=instance, plan=default_plan)

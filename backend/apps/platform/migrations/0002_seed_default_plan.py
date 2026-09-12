import uuid

from django.db import migrations


def seed_default_plan_and_backfill_tenants(apps, schema_editor):
    SubscriptionPlan = apps.get_model("platform", "SubscriptionPlan")
    TenantSubscription = apps.get_model("platform", "TenantSubscription")
    Tenant = apps.get_model("tenancy", "Tenant")

    # Import path, not the frozen historical model -- MODULE_CATALOGUE is
    # fixed platform code (see apps/platform/catalogue.py), not something a
    # migration should ever hardcode a stale copy of.
    from apps.platform.catalogue import MODULE_CATALOGUE

    plan, _ = SubscriptionPlan.objects.get_or_create(
        name="Full Access",
        defaults={
            "id": uuid.uuid4(),
            "module_codes": list(MODULE_CATALOGUE),
            "is_default": True,
            "is_active": True,
        },
    )

    for tenant in Tenant.objects.all():
        TenantSubscription.objects.get_or_create(tenant=tenant, defaults={"plan": plan})


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("platform", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_default_plan_and_backfill_tenants, noop_reverse),
    ]

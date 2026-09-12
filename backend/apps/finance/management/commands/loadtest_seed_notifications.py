import json
import uuid
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.notifications.models import NotificationChannel
from apps.notifications.services import enqueue_notification
from apps.tenancy.models import Tenant

from .loadtest_provision import DEFAULT_MANIFEST_PATH


class Command(BaseCommand):
    """Populates a NotificationOutbox backlog for the 5E Redis-outage chaos
    scenario via the real enqueue_notification() service -- never a
    load-test-only HTTP endpoint or a raw insert, both of which could bypass
    the idempotency/replay-validation logic 5D.1 hardened.
    """

    help = "Seed a NotificationOutbox backlog for the Milestone 5E Redis-outage chaos scenario."

    def add_arguments(self, parser):
        parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
        parser.add_argument("--count-per-tenant", type=int, default=500)

    def handle(self, *args, **options):
        manifest = json.loads(Path(options["manifest"]).read_text())
        total = 0
        for tenant_entry in manifest["tenants"]:
            tenant = Tenant.objects.get(slug=tenant_entry["slug"])
            for _ in range(options["count_per_tenant"]):
                enqueue_notification(
                    tenant=tenant, channel=NotificationChannel.SMS, recipient="0700000000",
                    message_type="loadtest_seed", idempotency_key=f"loadtest-seed:{tenant_entry['slug']}:{uuid.uuid4()}",
                    context={"seed": True},
                )
                total += 1
            self.stdout.write(f"Seeded {options['count_per_tenant']} notifications for {tenant_entry['slug']}")
        self.stdout.write(self.style.SUCCESS(f"Seeded {total} notifications total"))

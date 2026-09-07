from django.core.management.base import BaseCommand

from apps.tenancy.models import Tenant

from ...services import find_orphaned_storage_keys
from ...storage import resolve_storage_backend


class Command(BaseCommand):
    """Reconciles the filesystem/DB dual-write gap that can't be solved
    transactionally: a physical file can be written by upload_document
    just before the outer domain-attachment transaction rolls back,
    leaving an orphaned file with no matching Document row. Run manually
    or on a schedule -- never automatic, since deleting a file is
    irreversible.
    """

    help = "Delete storage-backend files with no matching Document row, per tenant."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        backend = resolve_storage_backend()
        total = 0
        for tenant in Tenant.objects.all():
            for key in find_orphaned_storage_keys(tenant=tenant):
                total += 1
                self.stdout.write(f"{tenant.slug}: {key}")
                if not options["dry_run"]:
                    backend.delete(tenant=tenant, key=key)
        verb = "Would remove" if options["dry_run"] else "Removed"
        self.stdout.write(self.style.SUCCESS(f"{verb} {total} orphaned file(s)"))

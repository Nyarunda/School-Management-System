from django.core.management.base import BaseCommand

from apps.tenancy.models import Tenant

from ...services import find_orphaned_storage_keys, purge_orphaned_documents


class Command(BaseCommand):
    """Reconciles the filesystem/DB dual-write gap that can't be solved
    transactionally: a physical file can be written by upload_document
    just before the outer domain-attachment transaction rolls back,
    leaving an orphaned file with no matching Document row. Also runs
    automatically via apps.documents.tasks.purge_orphaned_documents_task
    (RC Area 3) -- this command remains for on-demand/dry-run use.
    """

    help = "Delete storage-backend files with no matching Document row, per tenant."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if options["dry_run"]:
            total = 0
            for tenant in Tenant.objects.all():
                for key in find_orphaned_storage_keys(tenant=tenant):
                    total += 1
                    self.stdout.write(f"{tenant.slug}: {key}")
            self.stdout.write(self.style.SUCCESS(f"Would remove {total} orphaned file(s)"))
            return
        total = purge_orphaned_documents()
        self.stdout.write(self.style.SUCCESS(f"Removed {total} orphaned file(s)"))

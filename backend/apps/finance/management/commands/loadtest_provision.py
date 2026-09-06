import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.academics.models import AcademicLevel, AcademicYear
from apps.students.models import Student
from apps.tenancy.models import Membership, Role, Tenant, User

from ...models import FeeCategory, FeeItem, FeeStructure, InvoiceStatus, NumberSeries
from ...mpesa_services import configure_mpesa_gateway
from ...services import (
    add_fee_structure_line,
    approve_fee_structure,
    assign_fee_structure,
    create_fee_structure,
    generate_invoice,
    issue_invoice,
)

# Every finance.* permission string used anywhere in this app. Disposable
# load-test fixture data -- unlike a real school's role, there's no reason
# to scope this down.
ALL_FINANCE_PERMISSIONS = [
    "finance.allocation.reverse", "finance.credit_note.create",
    "finance.fee_structure.approve", "finance.fee_structure.create", "finance.fee_structure.edit", "finance.fee_structure.view",
    "finance.invoice.create", "finance.invoice.issue", "finance.invoice.view",
    "finance.mpesa.callback.process", "finance.mpesa.callback.verify", "finance.mpesa.callback.view",
    "finance.mpesa.configure",
    "finance.mpesa.stk_push.initiate", "finance.mpesa.stk_push.query", "finance.mpesa.stk_push.reconcile", "finance.mpesa.stk_push.view",
    "finance.payment.allocate", "finance.payment.record", "finance.payment.reverse", "finance.payment.view",
    "finance.reconciliation.ignore", "finance.reconciliation.ingest", "finance.reconciliation.match", "finance.reconciliation.view",
    "finance.setup.manage", "finance.setup.view",
    "finance.student_account.view",
]

DEFAULT_PASSWORD = "loadtest-pass-not-for-production"
DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parents[4] / "loadtest" / "manifest.json"
FEE_AMOUNT = Decimal("10000.00")


class Command(BaseCommand):
    help = "Provision tenants, students, invoices, and M-Pesa configuration for a Milestone 5E load-test run."

    def add_arguments(self, parser):
        parser.add_argument("--tenants", type=int, default=20, help="Number of tenants to provision.")
        parser.add_argument("--students-per-tenant", type=int, default=50)
        parser.add_argument("--noisy-multiplier", type=int, default=10,
                            help="Tenant 0 gets this many times the usual student pool, as the noisy neighbor.")
        parser.add_argument("--output", default=str(DEFAULT_MANIFEST_PATH))

    def handle(self, *args, **options):
        manifest = {"tenants": []}
        for index in range(options["tenants"]):
            is_noisy = index == 0
            student_count = options["students_per_tenant"] * (options["noisy_multiplier"] if is_noisy else 1)
            manifest["tenants"].append(self._provision_tenant(index=index, student_count=student_count, is_noisy=is_noisy))
            self.stdout.write(f"Provisioned {manifest['tenants'][-1]['slug']} ({student_count} students, noisy={is_noisy})")

        output_path = Path(options["output"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(manifest, indent=2))
        self.stdout.write(self.style.SUCCESS(f"Wrote manifest for {len(manifest['tenants'])} tenants to {output_path}"))

    @transaction.atomic
    def _provision_tenant(self, *, index, student_count, is_noisy):
        slug = f"loadtest-{index}"
        tenant, _ = Tenant.objects.get_or_create(slug=slug, defaults={"name": f"Load Test School {index}"})

        bursar, created = User.objects.get_or_create(username=f"loadtest-bursar-{index}")
        if created:
            bursar.set_password(DEFAULT_PASSWORD)
            bursar.save(update_fields=["password"])
        role, _ = Role.objects.get_or_create(tenant=tenant, name="Load Test Bursar", defaults={"permissions": ALL_FINANCE_PERMISSIONS})
        if role.permissions != ALL_FINANCE_PERMISSIONS:
            role.permissions = ALL_FINANCE_PERMISSIONS
            role.save(update_fields=["permissions"])
        Membership.objects.get_or_create(tenant=tenant, user=bursar, role=role)

        NumberSeries.objects.get_or_create(tenant=tenant, document_type="RECEIPT", defaults={"prefix": f"LT{index}-RCT-", "padding": 6})
        NumberSeries.objects.get_or_create(tenant=tenant, document_type="INVOICE", defaults={"prefix": f"LT{index}-INV-", "padding": 6})

        config = configure_mpesa_gateway(
            user=bursar, tenant=tenant, environment="SANDBOX", shortcode="600000",
            consumer_key=f"loadtest-key-{index}", consumer_secret=f"loadtest-secret-{index}", passkey=f"loadtest-passkey-{index}",
        )

        year, _ = AcademicYear.objects.get_or_create(
            tenant=tenant, name="Load Test Year", defaults={"starts_on": date(2026, 1, 1), "ends_on": date(2026, 12, 31)},
        )
        level, _ = AcademicLevel.objects.get_or_create(tenant=tenant, code="LT", defaults={"name": "Load Test Level", "sequence": 1})
        category, _ = FeeCategory.objects.get_or_create(tenant=tenant, code="LT", defaults={"name": "Load Test Fees"})
        item, _ = FeeItem.objects.get_or_create(tenant=tenant, category=category, code="TUITION", defaults={"name": "Tuition"})

        structure = _get_or_create_fee_structure(user=bursar, tenant=tenant, year=year, level=level, item=item)

        students = []
        for n in range(student_count):
            admission_number = f"LT{index}-{n:05d}"
            student, _ = Student.objects.get_or_create(
                tenant=tenant, admission_number=admission_number, defaults={"first_name": "Load", "last_name": f"Test{n}"},
            )
            assignment = assign_fee_structure(user=bursar, tenant=tenant, student=student, fee_structure=structure)
            invoice = generate_invoice(user=bursar, tenant=tenant, assignment=assignment)
            if invoice.status == InvoiceStatus.DRAFT:
                issue_invoice(user=bursar, tenant=tenant, invoice=invoice)
                invoice.refresh_from_db()
            students.append({"admission_number": admission_number, "student_id": str(student.id), "invoice_id": str(invoice.id)})

        return {
            "slug": slug, "is_noisy": is_noisy, "callback_token": config.callback_token,
            "bursar_username": bursar.username, "bursar_password": DEFAULT_PASSWORD,
            "students": students,
        }


def _get_or_create_fee_structure(*, user, tenant, year, level, item):
    """One shared, approved fee structure per tenant -- created on first
    provisioning of that tenant, reused on every re-run (loadtest_provision
    is idempotent, mirroring configure_mpesa_gateway's own idempotency).
    """
    structure = FeeStructure.objects.filter(tenant=tenant, academic_year=year, academic_level=level).first()
    if structure is None:
        structure = create_fee_structure(user=user, tenant=tenant, name="Load Test Structure", academic_year=year, academic_level=level)
    if not structure.lines.exists():
        add_fee_structure_line(user=user, tenant=tenant, fee_structure=structure, fee_item=item, amount=FEE_AMOUNT)
    if not structure.is_approved:
        approve_fee_structure(user=user, tenant=tenant, fee_structure=structure)
    return structure

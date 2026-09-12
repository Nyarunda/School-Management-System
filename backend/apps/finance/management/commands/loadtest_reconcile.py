import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from apps.students.models import Student
from apps.tenancy.models import Tenant

from ...loadtest_reconcile_checks import run_all_checks
from ...models import IncomingPayment, Invoice
from ...selectors import student_balance
from ...services import _invoice_outstanding_balance
from .loadtest_provision import DEFAULT_MANIFEST_PATH

REPO_ROOT = Path(__file__).resolve().parents[4] / "loadtest"


class Command(BaseCommand):
    """The Milestone 5E hard pass/fail gate: compares the harness's own
    record of every provider event it sent against the database state that
    resulted, per apps/finance/loadtest_reconcile_checks.py. See that
    module's docstring for why no check here assumes a student's ledger
    sums to zero.
    """

    help = "Verify the Milestone 5E financial reconciliation invariant for one run."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", default=None)
        parser.add_argument("--events-file", default=None,
                            help="Overrides the default <run-id>-events.json path. Pass the seeded_verified_callbacks.json path for a 5E-2 run.")
        parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))

    def handle(self, *args, **options):
        events_path = self._resolve_events_path(options)
        events = json.loads(events_path.read_text())
        manifest = json.loads(Path(options["manifest"]).read_text())

        incoming_rows, payment_rows, ownership_rows, expected_tenant_by_key = self._gather_payment_side(events)
        expected_balances, actual_balances, per_invoice_outstanding = self._gather_balance_side(manifest)

        violations = run_all_checks(
            events=events, incoming_payments=incoming_rows, payments=payment_rows,
            expected_balances=expected_balances, actual_balances=actual_balances,
            per_invoice_outstanding=per_invoice_outstanding, ownership_rows=ownership_rows,
            ownership_expected_tenant_by_key=expected_tenant_by_key,
        )

        run_id = options["run_id"] or events_path.stem.replace("-events", "")
        self._write_verdict(run_id, violations)

        if violations:
            self.stdout.write(self.style.ERROR(f"FAIL: {len(violations)} violation(s)"))
            for violation in violations:
                self.stdout.write(f"  - {violation.check}: {violation.detail}")
            raise CommandError(f"Reconciliation failed with {len(violations)} violation(s)")
        self.stdout.write(self.style.SUCCESS("PASS: no reconciliation violations"))

    def _resolve_events_path(self, options):
        if options["events_file"]:
            return Path(options["events_file"])
        if not options["run_id"]:
            raise CommandError("Pass --run-id or --events-file")
        return REPO_ROOT / "events" / f"{options['run_id']}-events.json"

    def _gather_payment_side(self, events):
        """Builds the IncomingPayment/Payment rows the checks module needs,
        and the expected-tenant map for the ownership check -- keyed by
        (tenant-independent) trans_id so both the IncomingPayment and its
        resulting Payment are checked against the tenant the harness
        actually sent that event to, not against each other (which would be
        circular).
        """
        trans_ids = [event["trans_id"] for event in events]
        expected_tenant_by_key = {("event", event["trans_id"]): event["tenant"] for event in events}

        incoming_rows = []
        payment_rows = []
        ownership_rows = []
        incoming_qs = IncomingPayment.objects.filter(external_transaction_id__in=trans_ids).select_related("tenant", "matched_payment")
        for incoming in incoming_qs:
            incoming_rows.append({
                "tenant": incoming.tenant.slug, "external_transaction_id": incoming.external_transaction_id, "amount": str(incoming.amount),
            })
            ownership_rows.append({"tenant": incoming.tenant.slug, "key": ("event", incoming.external_transaction_id)})
            if incoming.matched_payment_id:
                payment = incoming.matched_payment
                allocated_total = payment.allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0")
                payment_rows.append({
                    "tenant": incoming.tenant.slug, "payment_id": str(payment.id), "amount": str(payment.amount),
                    "allocated_total": str(allocated_total), "unapplied": str(payment.amount - allocated_total),
                })
                ownership_rows.append({"tenant": payment.tenant.slug, "key": ("event", incoming.external_transaction_id)})
        return incoming_rows, payment_rows, ownership_rows, expected_tenant_by_key

    def _gather_balance_side(self, manifest):
        """Per-student/per-invoice balances, computed via two genuinely
        independent code paths: _invoice_outstanding_balance (derived from
        Invoice/PaymentAllocation/CreditNote/AllocationReversal) vs
        selectors.student_balance (derived from StudentLedgerEntry). Neither
        is asserted to be zero -- these are real outstanding balances after
        partial payment, and comparing the two independent derivations is
        the actual invariant, not a hardcoded formula.
        """
        expected_balances = []
        actual_balances = []
        per_invoice_outstanding = []
        for tenant_entry in manifest["tenants"]:
            tenant = Tenant.objects.get(slug=tenant_entry["slug"])
            for student_entry in tenant_entry["students"]:
                student = Student.objects.get(tenant=tenant, pk=student_entry["student_id"])
                invoice = Invoice.objects.get(tenant=tenant, pk=student_entry["invoice_id"])
                outstanding = _invoice_outstanding_balance(tenant=tenant, invoice=invoice)
                expected_balances.append({"tenant": tenant_entry["slug"], "student_id": str(student.id), "balance": str(outstanding)})
                per_invoice_outstanding.append({"tenant": tenant_entry["slug"], "invoice_id": str(invoice.id), "outstanding": str(outstanding)})
                actual_balances.append({"tenant": tenant_entry["slug"], "student_id": str(student.id), "balance": str(student_balance(tenant=tenant, student=student))})
        return expected_balances, actual_balances, per_invoice_outstanding

    def _write_verdict(self, run_id, violations):
        reports_dir = REPO_ROOT / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        payload = {"run_id": run_id, "violations": [{"check": v.check, "detail": v.detail} for v in violations]}
        (reports_dir / f"{run_id}-verdict.json").write_text(json.dumps(payload, indent=2))

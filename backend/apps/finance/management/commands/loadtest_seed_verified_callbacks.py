import json
import secrets
from pathlib import Path

import requests
from django.core.management.base import BaseCommand

from apps.tenancy.models import Tenant, User

from ...models import MpesaCallbackLog, MpesaCallbackType
from ...mpesa_services import verify_mpesa_callback
from .loadtest_provision import DEFAULT_MANIFEST_PATH


class Command(BaseCommand):
    """Milestone 5E-2 setup step: creates real MpesaCallbackLog rows through
    the actual public webhook (real HTTP, not a shortcut) and verifies each
    one through the real verify_mpesa_callback() service -- all *before* the
    timed 5E-2 window starts, so that window measures only /process/ and
    never bypasses the verify prerequisite production traffic can't skip.
    """

    help = "Seed pre-verified M-Pesa callbacks for the Milestone 5E-2 processing-capacity phase."

    def add_arguments(self, parser):
        parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
        parser.add_argument("--base-url", default="http://localhost:8000")
        parser.add_argument("--count-per-tenant", type=int, default=200)
        parser.add_argument("--amount", default="500")

    def handle(self, *args, **options):
        manifest = json.loads(Path(options["manifest"]).read_text())
        seeded = []
        for tenant_entry in manifest["tenants"]:
            tenant = Tenant.objects.get(slug=tenant_entry["slug"])
            bursar = User.objects.get(username=tenant_entry["bursar_username"])
            students = tenant_entry["students"]
            if not students:
                continue
            count = options["count_per_tenant"]
            for n in range(count):
                student = students[n % len(students)]
                trans_id = f"LTSEED-{tenant_entry['slug']}-{secrets.token_hex(6)}"
                response = requests.post(
                    f"{options['base_url']}/api/v1/finance/mpesa/{tenant_entry['callback_token']}/c2b/confirmation/",
                    json={"TransID": trans_id, "TransAmount": options["amount"], "BillRefNumber": student["admission_number"]},
                    timeout=10,
                )
                response.raise_for_status()
                callback = MpesaCallbackLog.objects.get(
                    tenant=tenant, callback_type=MpesaCallbackType.C2B_CONFIRMATION, provider_transaction_id=trans_id,
                )
                verify_mpesa_callback(user=bursar, tenant=tenant, callback_id=callback.id, evidence="loadtest-seed-verification")
                seeded.append({
                    "tenant": tenant_entry["slug"], "callback_id": str(callback.id), "trans_id": trans_id,
                    "amount": options["amount"], "admission_number": student["admission_number"],
                })
            self.stdout.write(f"Seeded {count} verified callbacks for {tenant_entry['slug']}")

        out_path = Path(options["manifest"]).parent / "seeded_verified_callbacks.json"
        out_path.write_text(json.dumps(seeded, indent=2))
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(seeded)} seeded callback records to {out_path}"))

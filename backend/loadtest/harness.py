"""Milestone 5E load-driving harness: real HTTP against a running
docker-compose stack (never in-process calls), so the actual webhook/DRF/
auth path is under test. Plain script -- no Django import, only httpx for
async HTTP concurrency (see backend/requirements/loadtest.txt).

Three measurement phases (see docs/architecture/load-testing.md for the
full runbook):
  5e1 -- callback-ingestion capacity: only public C2B confirmations.
  5e2 -- financial-processing capacity: /process/ only, against a batch
         loadtest_seed_verified_callbacks already verified out-of-band.
  5e3 -- sustainable end-to-end: 5e1's ramp + a bounded operator pool
         (poll RECEIVED -> verify -> process) + a low STK-push trickle.
  smoke -- tiny fixed rate, ~30s, phase 5e3 shape, for a fast sanity check
         of the tooling itself before a real ramp run.

Writes, per run:
  loadtest/events/<run_id>-events.json  -- the harness's own record of
    every provider event it sent (tenant, trans_id, amount, admission
    number, expect_payment) -- the input loadtest_reconcile compares
    database state against.
  loadtest/reports/<run_id>-metrics.json -- raw per-request latency/status
    samples for report.py to summarize.
"""
import argparse
import asyncio
import itertools
import json
import threading
import time
import uuid
from pathlib import Path

import httpx

from loadtest import chaos

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = REPO_ROOT / "manifest.json"
EVENTS_DIR = REPO_ROOT / "events"
REPORTS_DIR = REPO_ROOT / "reports"


class Recorder:
    """Collects the two things a run produces. Plain lists appended to from
    a single-threaded asyncio event loop -- no lock needed; the chaos
    thread never touches this.
    """

    def __init__(self):
        self.events = []
        self.metrics = []

    def record_event(self, **event):
        self.events.append(event)

    def record_metric(self, *, traffic_class, tenant, latency_ms, status_code, phase):
        self.metrics.append({
            "traffic_class": traffic_class, "tenant": tenant, "latency_ms": latency_ms,
            "status_code": status_code, "phase": phase, "ts": time.time(),
        })

    def write(self, run_id):
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (EVENTS_DIR / f"{run_id}-events.json").write_text(json.dumps(self.events, indent=2))
        (REPORTS_DIR / f"{run_id}-metrics.json").write_text(json.dumps(self.metrics, indent=2))


def load_manifest(path):
    return json.loads(Path(path).read_text())


def parse_ramp(spec):
    return [int(part) for part in spec.split(",") if part.strip()]


async def _timed_request(client, method, url, **kwargs):
    start = time.monotonic()
    try:
        response = await client.request(method, url, **kwargs)
        latency_ms = (time.monotonic() - start) * 1000
        return response.status_code, latency_ms
    except httpx.HTTPError:
        latency_ms = (time.monotonic() - start) * 1000
        return None, latency_ms


def pick_student(tenant_entry, *, counter, hot_invoice_fraction):
    students = tenant_entry["students"]
    if hot_invoice_fraction > 0 and (counter % 1000) / 1000 < hot_invoice_fraction:
        return students[0]
    return students[counter % len(students)]


async def send_c2b_confirmation(client, base_url, tenant_entry, *, run_id, counter, amount, recorder, phase,
                                hot_invoice_fraction, duplicate_storm, storm_size, storm_counter):
    student = pick_student(tenant_entry, counter=counter, hot_invoice_fraction=hot_invoice_fraction)
    if duplicate_storm and counter % storm_size != 0:
        trans_id = f"LT-{run_id}-{tenant_entry['slug']}-storm-{storm_counter}"
    else:
        trans_id = f"LT-{run_id}-{tenant_entry['slug']}-{counter}"
        if duplicate_storm:
            storm_counter = counter
    url = f"{base_url}/api/v1/finance/mpesa/{tenant_entry['callback_token']}/c2b/confirmation/"
    payload = {"TransID": trans_id, "TransAmount": str(amount), "BillRefNumber": student["admission_number"]}
    status_code, latency_ms = await _timed_request(client, "POST", url, json=payload, timeout=15)
    recorder.record_metric(traffic_class="c2b_confirmation", tenant=tenant_entry["slug"], latency_ms=latency_ms, status_code=status_code, phase=phase)
    recorder.record_event(
        tenant=tenant_entry["slug"], trans_id=trans_id, amount=str(amount),
        admission_number=student["admission_number"], phase=phase,
        # 5e1 measures raw ingestion only -- no operator pool runs, so
        # nothing ever verifies/processes these, and loadtest_reconcile
        # must not flag that as a missing payment. 5e3/smoke run the
        # operator pool alongside the ramp, so a payment IS expected there.
        expect_payment=phase != "5e1",
    )
    return storm_counter


async def run_ramp(client, base_url, manifest, *, run_id, ramp, step_duration, amount, recorder, phase,
                   hot_invoice_fraction, duplicate_storm, storm_size, concurrency_limit):
    semaphore = asyncio.Semaphore(concurrency_limit)
    tenants = manifest["tenants"]
    weights = [10 if t["is_noisy"] else 1 for t in tenants]
    total_weight = sum(weights)
    counters = {t["slug"]: itertools.count() for t in tenants}
    storm_counters = {t["slug"]: 0 for t in tenants}

    async def bounded_send(tenant_entry):
        async with semaphore:
            counter = next(counters[tenant_entry["slug"]])
            storm_counters[tenant_entry["slug"]] = await send_c2b_confirmation(
                client, base_url, tenant_entry, run_id=run_id, counter=counter, amount=amount, recorder=recorder,
                phase=phase, hot_invoice_fraction=hot_invoice_fraction, duplicate_storm=duplicate_storm,
                storm_size=storm_size, storm_counter=storm_counters[tenant_entry["slug"]],
            )

    for step_rate in ramp:
        print(f"[harness] {phase}: ramping to {step_rate} events/min for {step_duration}s")
        end_time = time.monotonic() + step_duration
        tasks = []
        while time.monotonic() < end_time:
            for tenant_entry, weight in zip(tenants, weights):
                tenant_rate_per_minute = step_rate * weight / total_weight
                if tenant_rate_per_minute <= 0:
                    continue
                interval = 60.0 / tenant_rate_per_minute
                tasks.append(asyncio.create_task(bounded_send(tenant_entry)))
                await asyncio.sleep(interval / len(tenants))
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_operator_pool(client, base_url, tenant_entry, *, operators, phase, recorder, stop_event):
    auth = httpx.BasicAuth(tenant_entry["bursar_username"], tenant_entry["bursar_password"])
    headers = {"X-Tenant-Slug": tenant_entry["slug"]}

    async def worker():
        while not stop_event.is_set():
            status_code, latency_ms = await _timed_request(
                client, "GET", f"{base_url}/api/v1/finance/mpesa/callbacks/?status=RECEIVED&page_size=20",
                auth=auth, headers=headers, timeout=15,
            )
            recorder.record_metric(traffic_class="callback_list", tenant=tenant_entry["slug"], latency_ms=latency_ms, status_code=status_code, phase=phase)
            if status_code != 200:
                await asyncio.sleep(1)
                continue
            # A fresh request is needed to read the body; _timed_request only
            # returns status/latency, so re-fetch here deliberately (keeps
            # the hot polling loop's shape simple, at the cost of one call).
            response = await client.get(
                f"{base_url}/api/v1/finance/mpesa/callbacks/?status=RECEIVED&page_size=20", auth=auth, headers=headers, timeout=15,
            )
            results = response.json().get("results", []) if response.status_code == 200 else []
            for item in results:
                callback_id = item["id"]
                status_code, latency_ms = await _timed_request(
                    client, "POST", f"{base_url}/api/v1/finance/mpesa/callbacks/{callback_id}/verify/",
                    auth=auth, headers=headers, json={"evidence": "loadtest-operator-verification"}, timeout=15,
                )
                recorder.record_metric(traffic_class="callback_verify", tenant=tenant_entry["slug"], latency_ms=latency_ms, status_code=status_code, phase=phase)
                status_code, latency_ms = await _timed_request(
                    client, "POST", f"{base_url}/api/v1/finance/mpesa/callbacks/{callback_id}/process/",
                    auth=auth, headers=headers, timeout=15,
                )
                recorder.record_metric(traffic_class="callback_process", tenant=tenant_entry["slug"], latency_ms=latency_ms, status_code=status_code, phase=phase)
            if not results:
                await asyncio.sleep(0.5)

    await asyncio.gather(*(worker() for _ in range(operators)))


async def run_stk_trickle(client, base_url, tenant_entry, *, rate_per_minute, phase, recorder, stop_event):
    auth = httpx.BasicAuth(tenant_entry["bursar_username"], tenant_entry["bursar_password"])
    headers = {"X-Tenant-Slug": tenant_entry["slug"]}
    interval = 60.0 / rate_per_minute
    counter = itertools.count()
    while not stop_event.is_set():
        n = next(counter)
        student = tenant_entry["students"][n % len(tenant_entry["students"])]
        payload = {
            "student": student["student_id"], "phone_number": "0712345678", "amount": "500",
            "idempotency_key": f"loadtest-stk-{tenant_entry['slug']}-{uuid.uuid4()}",
        }
        status_code, latency_ms = await _timed_request(
            client, "POST", f"{base_url}/api/v1/finance/mpesa/stk-push/", auth=auth, headers=headers, json=payload, timeout=15,
        )
        recorder.record_metric(traffic_class="stk_push", tenant=tenant_entry["slug"], latency_ms=latency_ms, status_code=status_code, phase=phase)
        await asyncio.sleep(interval)


async def run_5e2(client, base_url, seeded_path, *, ramp, step_duration, recorder, concurrency_limit):
    seeded = json.loads(Path(seeded_path).read_text())
    by_tenant = {}
    for item in seeded:
        by_tenant.setdefault(item["tenant"], []).append(item)
    manifest_by_slug = {}
    for tenant_entry in load_manifest(DEFAULT_MANIFEST)["tenants"]:
        manifest_by_slug[tenant_entry["slug"]] = tenant_entry

    semaphore = asyncio.Semaphore(concurrency_limit)
    cursors = {tenant: itertools.cycle(items) for tenant, items in by_tenant.items()}

    async def process_one(tenant_slug):
        tenant_entry = manifest_by_slug[tenant_slug]
        auth = httpx.BasicAuth(tenant_entry["bursar_username"], tenant_entry["bursar_password"])
        headers = {"X-Tenant-Slug": tenant_slug}
        item = next(cursors[tenant_slug])
        async with semaphore:
            status_code, latency_ms = await _timed_request(
                client, "POST", f"{base_url}/api/v1/finance/mpesa/callbacks/{item['callback_id']}/process/",
                auth=auth, headers=headers, timeout=15,
            )
        recorder.record_metric(traffic_class="callback_process", tenant=tenant_slug, latency_ms=latency_ms, status_code=status_code, phase="5e2")
        # The ramp cycles through the seeded batch, possibly re-processing
        # the same item more than once (a harmless no-op per
        # process_mpesa_callback's own idempotency) -- recording the event
        # every call is still correct: check_no_duplicate_or_missing_payments
        # groups by trans_id, so repeats still collapse to "exactly one
        # IncomingPayment expected", matching the duplicate-storm handling.
        recorder.record_event(
            tenant=tenant_slug, trans_id=item["trans_id"], amount=item["amount"],
            admission_number=item["admission_number"], phase="5e2", expect_payment=True,
        )

    for step_rate in ramp:
        print(f"[harness] 5e2: ramping to {step_rate} events/min for {step_duration}s")
        end_time = time.monotonic() + step_duration
        tasks = []
        tenants = list(by_tenant)
        while time.monotonic() < end_time:
            for tenant_slug in tenants:
                interval = 60.0 / (step_rate / len(tenants))
                tasks.append(asyncio.create_task(process_one(tenant_slug)))
                await asyncio.sleep(interval / len(tenants))
        await asyncio.gather(*tasks, return_exceptions=True)


async def drain_backlog(client, base_url, manifest, *, max_wait_seconds, poll_interval=1.0):
    """Polls each tenant's RECEIVED-callback count until it hits zero or
    `max_wait_seconds` elapses, so the operator pool gets a genuine chance
    to catch up on the tail of the ramp before the run is considered over.
    """
    deadline = time.monotonic() + max_wait_seconds
    while time.monotonic() < deadline:
        totals = []
        for tenant_entry in manifest["tenants"]:
            auth = httpx.BasicAuth(tenant_entry["bursar_username"], tenant_entry["bursar_password"])
            headers = {"X-Tenant-Slug": tenant_entry["slug"]}
            try:
                response = await client.get(
                    f"{base_url}/api/v1/finance/mpesa/callbacks/?status=RECEIVED&page_size=1",
                    auth=auth, headers=headers, timeout=15,
                )
                totals.append(response.json().get("count", 0) if response.status_code == 200 else 0)
            except httpx.HTTPError:
                totals.append(0)
        if sum(totals) == 0:
            print("[harness] backlog drained")
            return
        print(f"[harness] draining backlog: {sum(totals)} callbacks still RECEIVED")
        await asyncio.sleep(poll_interval)
    print("[harness] drain timeout reached -- some callbacks may still be RECEIVED (a real capacity finding, not a bug in the check)")


async def main_async(args):
    manifest = load_manifest(args.manifest)
    run_id = args.run_id or f"{args.phase}-{int(time.time())}"
    recorder = Recorder()

    stop_event = asyncio.Event()
    if args.chaos:
        threading.Thread(
            target=chaos.run_scenario, args=(args.chaos,),
            kwargs={"at_seconds": args.chaos_at, "duration_seconds": args.chaos_duration}, daemon=True,
        ).start()

    async with httpx.AsyncClient() as client:
        if args.phase == "5e1":
            await run_ramp(
                client, args.base_url, manifest, run_id=run_id, ramp=parse_ramp(args.ramp), step_duration=args.step_duration,
                amount=args.amount, recorder=recorder, phase="5e1", hot_invoice_fraction=args.hot_invoice_fraction,
                duplicate_storm=args.duplicate_storm, storm_size=args.storm_size, concurrency_limit=args.concurrency,
            )
        elif args.phase == "5e2":
            await run_5e2(
                client, args.base_url, args.seeded, ramp=parse_ramp(args.ramp), step_duration=args.step_duration,
                recorder=recorder, concurrency_limit=args.concurrency,
            )
        elif args.phase in ("5e3", "smoke"):
            ramp = [50] if args.phase == "smoke" else parse_ramp(args.ramp)
            step_duration = 30 if args.phase == "smoke" else args.step_duration
            operators = 2 if args.phase == "smoke" else args.operators
            background = []
            for tenant_entry in manifest["tenants"]:
                background.append(asyncio.create_task(run_operator_pool(
                    client, args.base_url, tenant_entry, operators=operators, phase=args.phase, recorder=recorder, stop_event=stop_event,
                )))
                background.append(asyncio.create_task(run_stk_trickle(
                    client, args.base_url, tenant_entry, rate_per_minute=args.stk_rate, phase=args.phase, recorder=recorder, stop_event=stop_event,
                )))
            await run_ramp(
                client, args.base_url, manifest, run_id=run_id, ramp=ramp, step_duration=step_duration,
                amount=args.amount, recorder=recorder, phase=args.phase, hot_invoice_fraction=args.hot_invoice_fraction,
                duplicate_storm=args.duplicate_storm, storm_size=args.storm_size, concurrency_limit=args.concurrency,
            )
            # The ramp finishing doesn't mean the operator pool has caught up
            # -- a callback received in the ramp's last second is still
            # legitimately RECEIVED, not lost. Cancelling immediately here
            # was found (via an actual smoke run, not just unit tests) to
            # make loadtest_reconcile misreport a drain-timing artifact as a
            # "missing payment". Wait for the backlog to actually clear
            # (bounded) before stopping the pool.
            await drain_backlog(client, args.base_url, manifest, max_wait_seconds=args.drain_timeout)
            stop_event.set()
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
        else:
            raise ValueError(f"Unknown phase: {args.phase}")

    recorder.write(run_id)
    print(f"[harness] run {run_id} complete: {len(recorder.events)} events, {len(recorder.metrics)} metric samples")
    print(f"[harness] next: python manage.py loadtest_reconcile --run-id {run_id}")


def parse_args():
    parser = argparse.ArgumentParser(description="Milestone 5E load-driving harness.")
    parser.add_argument("--phase", choices=["5e1", "5e2", "5e3", "smoke"], required=True)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--seeded", default=str(REPO_ROOT / "seeded_verified_callbacks.json"), help="Only for --phase 5e2.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--ramp", default="100,500,1000,2500,5000")
    parser.add_argument("--step-duration", type=int, default=300)
    parser.add_argument("--amount", default="500")
    parser.add_argument("--operators", type=int, default=10, help="Per-tenant verify/process pool size, phase 5e3 only.")
    parser.add_argument("--stk-rate", type=float, default=5, help="STK pushes/minute per tenant, phase 5e3 only.")
    parser.add_argument("--drain-timeout", type=float, default=60,
                        help="Max seconds to wait for the operator pool to clear the RECEIVED backlog after the ramp ends, phase 5e3/smoke only.")
    parser.add_argument("--concurrency", type=int, default=200, help="Max in-flight ramp requests at once.")
    parser.add_argument("--hot-invoice-fraction", type=float, default=0.0)
    parser.add_argument("--duplicate-storm", action="store_true")
    parser.add_argument("--storm-size", type=int, default=20)
    parser.add_argument("--chaos", choices=sorted(chaos.SCENARIOS), default=None)
    parser.add_argument("--chaos-at", type=float, default=60)
    parser.add_argument("--chaos-duration", type=float, default=60)
    parser.add_argument("--run-id", default=None)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))

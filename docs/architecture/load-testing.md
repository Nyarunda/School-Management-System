# Milestone 5E: payment load & capacity validation

This is a manually-run tool, not a CI gate. It answers two separate questions every run: where does this deployment's throughput start to degrade (a *finding*, not a pass/fail threshold — nobody has established a target yet), and did every shilling the run generated reconcile correctly afterward (a **hard pass/fail gate**, always).

## What it is not

Not wired into CI. Not a PostgreSQL/pgbouncer/gunicorn tuning exercise (their absence is a deliberate, documented finding this tool is built to surface, not fix). Not a substitute for the real Daraja sandbox smoke test — this always runs against `backend/loadtest/fake_daraja.py`, a stub, never the real internet.

## One-time setup

```powershell
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up --build -d
cd backend
python manage.py migrate
python manage.py loadtest_provision --tenants 20 --students-per-tenant 50
```

`loadtest_provision` is idempotent (re-running it is safe) and writes `backend/loadtest/manifest.json` — tenant slugs, callback tokens, and bursar Basic-Auth credentials. Tenant `loadtest-0` is always the noisy neighbor (a 10x larger student pool by default).

## Sanity check before a real run

```powershell
python -m loadtest.harness --phase smoke
python manage.py loadtest_reconcile --run-id <the run id the harness printed>
```

Confirms provisioning, traffic generation, and reconciliation all work end-to-end at a tiny scale (~30s) before committing to a real ramp.

## The three measurement phases

Run each independently; each writes its own `<run-id>-events.json` / `<run-id>-metrics.json`.

**5E-1 — callback-ingestion capacity** (public webhook write path only, no verification/processing):
```powershell
python -m loadtest.harness --phase 5e1 --ramp 100,500,1000,2500,5000 --step-duration 300
```

**5E-2 — financial-processing capacity** (isolates verified-callback -> Payment/Allocation/Ledger cost from ingestion cost — verification happens for real, just *before* the timed window):
```powershell
python manage.py loadtest_seed_verified_callbacks --count-per-tenant 500
python -m loadtest.harness --phase 5e2 --ramp 100,500,1000,2500,5000 --step-duration 300
```

**5E-3 — sustainable end-to-end capacity** (the realistic pipeline: ramped C2B confirmations + a bounded per-tenant operator pool doing verify->process + a low STK-push trickle against the fake Daraja stub):
```powershell
python -m loadtest.harness --phase 5e3 --ramp 100,500,1000,2500,5000 --step-duration 300 --operators 10 --stk-rate 5
```

Add `--duplicate-storm --storm-size 20` to any phase to replay the same `TransID` repeatedly instead of unique ones. Add `--hot-invoice-fraction 0.5` to send half of all traffic at one pre-provisioned student instead of spreading across the pool.

## Failure injection

Pass `--chaos redis-outage` or `--chaos kill-worker` (with `--chaos-at <seconds>` and `--chaos-duration <seconds>`) to any harness invocation above — it schedules the injection against the running compose stack in a background thread while traffic keeps flowing. For the Redis-outage scenario, seed a backlog first so there's something to observe accumulating and draining:
```powershell
python manage.py loadtest_seed_notifications --count-per-tenant 500
python -m loadtest.harness --phase 5e3 --chaos redis-outage --chaos-at 60 --chaos-duration 120
```

A chaos scenario can also be run standalone for a manual check: `python -m loadtest.chaos redis-outage --at 0 --duration 60`.

## Server-side sampling

Run alongside any harness invocation, for roughly the same duration:
```powershell
python -m loadtest.sampler --run-id <same run id> --duration 1800
```

## After every run

```powershell
python manage.py loadtest_reconcile --run-id <run id>
python -m loadtest.report --run-id <run id>
```

`loadtest_reconcile` exits non-zero and lists every offending row if anything failed to reconcile — that is a real bug, gets its own follow-up commit, and blocks calling the run a pass regardless of how good the throughput numbers looked. `loadtest.report` renders `backend/loadtest/reports/<run-id>.md`: request latency percentiles and error rates per phase/traffic class, server-side PostgreSQL/backlog samples over time, and the reconciliation verdict.

## Known findings this tool is designed to surface, not fix here

No `CONN_MAX_AGE` on the database connection (a new PostgreSQL connection per request). No connection pooling (pgbouncer). Gunicorn worker/thread counts and Celery concurrency in `docker-compose.loadtest.yml` are defaults to tune per host, not auto-detected. If a run's samples show these are the actual ceiling, that is exactly the kind of finding this milestone exists to produce — fixing it is follow-up work, not part of this tool.

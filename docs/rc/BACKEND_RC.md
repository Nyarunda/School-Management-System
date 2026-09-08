# Backend Release Candidate — Evidence

Feature-freeze baseline: `b4d160f` (Milestone 22.4 — Tenant User & Role Administration; Milestone 22, Backend-Wide Hardening, complete). From this baseline, RC work fixes demonstrated defects and release blockers only — no unrelated functionality.

The RC gate is 8 acceptance areas, each closed with recorded, reproducible evidence — not "the test suite is green." Every finding is classified **BLOCKER** (cannot release), **DEFECT** (fix before RC approval), **ACCEPTED RISK** (understood and explicitly accepted), or **POST-GO-LIVE** (intentionally deferred).

## Area 1 — Build, configuration & migrations ✅ CLOSED

**Target commit under test:** `b4d160f82ef423fb2a79911abedc018588988549`
**Date:** 2026-09-08
**Tooling:** Docker 29.4.0, PostgreSQL 17 (`postgres:17-alpine`), Redis 7 (`redis:7-alpine`), Python 3.12.6, Django 5.2.5.
**Scripts:** [`scripts/rc/migration_gate.sh`](../../scripts/rc/migration_gate.sh), [`scripts/rc/docker_smoke_test.sh`](../../scripts/rc/docker_smoke_test.sh) — both plain git/docker/psql/curl, runnable by any developer or future CI, no session-specific tooling.

### Checklist results

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Clean PostgreSQL 17 → migrate from zero | PASS | `docs/rc/evidence/area1-migration-gate-b4d160f-20260908T181829Z.txt` |
| 2 | Representative existing database → migrate forward | PASS — source commit `12ba0c7` seeded via `loadtest_provision` (3 tenants, 5 students/tenant), forward-migrated to target; a checksum over tenant slugs, membership `(tenant,user,role,is_active)` tuples, student ids, and invoice `(id,total,status)` tuples was identical before and after (`cb36e8411356259551ab2a474726bb64`) | same file |
| 3 | `manage.py check --deploy` | PASS, run under a real `DJANGO_ENV=production` env with every required var actually set (not dev/test settings) | same file |
| 4 | Production Docker image build | PASS — built and tagged `school-management-backend:rc-b4d160f`, kept (not deleted) for unambiguous Git-SHA↔image identity | `docs/rc/evidence/area1-docker-smoke-b4d160f-20260908T182756Z.txt` |
| 5 | Gunicorn boot + graceful SIGTERM | PASS — `docker stop --time 30` exited code `0` in 2s (well under the bound — no SIGKILL escalation) | same file |
| 6 | Required environment/configuration validation | PASS (after fixing a defect — see below) | `config/test_production_settings.py`, 13/13 |
| 7 | No pending/uncommitted migrations | PASS — `makemigrations --check --dry-run` and `migrate --check` both clean | migration-gate evidence file |

### Bonus checks (beyond the original 7-item checklist, added during this area's review)
- `/readyz/` + `/healthz/` semantics verified under actual dependency failure, not just "the URL exists":

  | Condition | `/healthz/` | `/readyz/` |
  |---|---|---|
  | Everything healthy | 200 | 200, `status: ok` |
  | Redis down | 200 | 200, `status: degraded` |
  | PostgreSQL down | 200 | 503, `status: unhealthy` |
  | PostgreSQL recovers | 200 | 200 |

  This proves the fail-open/soft-dependency design from Milestone 22.3 (Redis is soft, Postgres is the hard dependency) under a real dependency outage, not just at the unit-test level.

### Findings

| Finding | Classification | Resolution |
|---|---|---|
| `POSTGRES_DB`/`USER`/`PASSWORD`/`HOST` were not validated in production and silently fell back to dev defaults (e.g. the dev password) if unset | **DEFECT** | Fixed this area: `config/settings.py` now requires all four explicitly and rejects the literal dev-default password. `DJANGO_CACHE_URL` is now also validated for scheme (`redis`/`rediss`) and hostname, not just presence. |
| No CI automation runs `makemigrations --check` / `migrate --check` / the two RC scripts | **ACCEPTED RISK** | No CI exists in this repo yet; building CI infrastructure is out of RC scope (an infra initiative, not a backend code defect). **Recorded process requirement**: every backend release candidate must run `scripts/rc/migration_gate.sh` and `scripts/rc/docker_smoke_test.sh` successfully before approval, until these are automated in CI. |
| DRF token lifetime has no expiry (`rest_framework.authtoken`, Milestone 22.4) | **ACCEPTED RISK**, tracked for Area 2 | Already an acknowledged limitation from 22.4; revisit if/when Area 2 (Authentication, tenancy & security) is planned. |

### Full test suite (SQLite + PostgreSQL 17)
Re-run in full as part of this area's verification (see session log) — all green, including the 3 new production-settings tests.

---

## Area 2 — Authentication, tenancy & security
*Not started.* Grounding already gathered from a retroactive review of Milestone 22.4 — see project memory `rc_area2_grounding_22_4_defects`. Known items to address: existing-user invite flow grants premature active access + incorrectly blocks legitimate acceptance (root cause: password state is User-level, acceptance is Membership-level); no dedicated login/invite-accept throttle scope; no Super Admin tenant-provisioning API.

## Area 3 — Critical business journeys
*Not started.*

## Area 4 — Finance & M-Pesa integrity
*Not started.*

## Area 5 — Concurrency & failure recovery
*Not started.*

## Area 6 — Performance & capacity
*Not started.*

## Area 7 — Operational resilience
*Not started.*

## Area 8 — RC evidence and decision
*Not started — this section becomes the final sign-off once Areas 1-7 close.*

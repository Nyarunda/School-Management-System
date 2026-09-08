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

## Area 2 — Authentication, tenancy & security ✅ CLOSED

**Baseline under test:** `91144e2` (RC Area 1 close). **Date:** 2026-09-09.
Grounding: a retroactive review of Milestone 22.4, verified claim-by-claim against actual code before this area was planned — see project memory `rc_area2_grounding_22_4_defects`.

### Findings

| Finding | Classification | Resolution |
|---|---|---|
| `invite_user` granted an *existing* user (already holding a usable password from another tenant) immediately-active `Membership` access before they ever visited the invite link; `accept_invite` then gated single-use replay protection on the user's *global* `has_usable_password()`, so that same existing user was wrongly told "This invite has already been accepted." | **DEFECT, fixed this area** | Every invite (new or existing user) now creates `Membership(is_active=False)`. A new `Membership.invite_accepted_at` field is the single-use acceptance gate, resolved via `(tenant_id, user_id)` from the token — independent of the user's global password state. A password is only required/touched for a genuinely new user. |
| Deactivating a membership *before* its invite was ever accepted, then replaying the still-valid (7-day) token, would reactivate it and set `invite_accepted_at` — overriding the admin's deactivation. | **ACCEPTED RISK** | Not fixed this area: closing it means teaching `deactivate_membership` to also invalidate outstanding invites, which is new behavior, not a fix for the demonstrated defect. Did not exist as a risk before this area's fix (new-user memberships weren't touched by `accept_invite`'s `is_active` at all). Revisit if it's ever demonstrated to matter in practice. |
| "Invite token only contains `user_id`, so a multi-tenant user's invite is ambiguous" (raised in the retroactive review) | **Debunked, no action** | False against actual code: `invite_user` signs both `user_id` and `tenant_id`, and `Membership` is unique per `(tenant, user)` — no ambiguity exists. Recorded here rather than silently dropped. |
| `_ensure_not_removing_last_administrator`'s locking strategy (raised in the retroactive review) | **No action needed** | Already correct: locks a stable `select_for_update().filter(tenant=tenant, is_active=True)` query, verified by a real passing PostgreSQL concurrency test. |
| `LoginView`/`InviteAcceptView` had no dedicated throttle scope, sharing the general `AnonRateThrottle` (100/hour) — far too generous for credential-verification endpoints | **DEFECT, fixed this area** | New `ScopedRateThrottle` scope `"login"` (5/min, env-overridable via `THROTTLE_RATE_LOGIN`), mirroring the existing `mpesa_callback` pattern. |
| No Super Admin API existed to provision a new tenant + its first administrator (shell/ORM-only) | **New capability, added this area** | `apps.platform.services.provision_tenant` (atomic: `Tenant` creation cooperates with the existing `provision_default_subscription` signal rather than duplicating it; creates the initial admin `Role` + reuses `invite_user` for the admin's `User`/`Membership`) behind a new `IsSuperUser`-gated `POST /api/v1/platform/tenants/`. The initial admin goes through the same consent-gated invite/accept flow as anyone else. |
| `DEFAULT_AUTHENTICATION_CLASSES` worry (implicit-default risk) | **Confirmed not a bug, no action** | Already explicit (`TokenAuthentication` + `SessionAuthentication`), not relying on DRF's implicit defaults. Kept both — `SessionAuthentication` supports the browsable API / same-origin session tooling and isn't the risk originally worried about. |
| DRF token lifetime has no expiry | **ACCEPTED RISK, carried from Area 1** | No expiry/rotation mechanism added this area — out of demonstrated-defect scope. |

### Test coverage
`apps/tenancy/tests.py` (new/updated `InviteAndAcceptTests` cases for the exact demonstrated bug and its replay behavior), `apps/tenancy/api_tests.py` (existing-user second-tenant accept round trip), `config/test_throttling.py` (new `LoginThrottleTests`), `apps/platform/tests.py`/`api_tests.py` (new `provision_tenant` service + API tests: single-subscription signal cooperation, admin-guard-permission requirement, non-superuser 403).

### Full test suite (SQLite + PostgreSQL 17)
SQLite: 779 tests, OK (41 skipped — Postgres-only cases). PostgreSQL 17 (`postgres:17-alpine`, disposable container): 779 tests, OK (0 skipped).

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

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

**Post-close note (added during Area 6, not reopening this area):** `LoginThrottleTests`' proof of the real `5/min` rate is a Django `TestClient`-driven unit test — single Python process, one shared cache instance — so it remains valid, unaffected evidence that the throttle *logic* enforces exactly `5/min`. What's newly caveated is a separate, informal observation from Milestone 5E setup: ~20 tenants logging in from one IP against the live multi-worker Docker stack tripped real `429`s, which at the time read as corroborating live proof of the `5/min` ceiling. Area 6 found that this exact stack was running DRF throttling against a per-Gunicorn-worker cache, not a shared one (see Area 6's defect below) — so those live `429`s were real, but came from several independent smaller buckets, not one true `5/min` gate. The true single-shared-cache production ceiling is *at least as strict* as `5/min`, likely stricter in effect than what the live stack appeared to allow before the fix. No action needed: the unit test already proves the number the code is supposed to enforce.

### Test coverage
`apps/tenancy/tests.py` (new/updated `InviteAndAcceptTests` cases for the exact demonstrated bug and its replay behavior), `apps/tenancy/api_tests.py` (existing-user second-tenant accept round trip), `config/test_throttling.py` (new `LoginThrottleTests`), `apps/platform/tests.py`/`api_tests.py` (new `provision_tenant` service + API tests: single-subscription signal cooperation, admin-guard-permission requirement, non-superuser 403).

### Full test suite (SQLite + PostgreSQL 17)
SQLite: 779 tests, OK (41 skipped — Postgres-only cases). PostgreSQL 17 (`postgres:17-alpine`, disposable container): 779 tests, OK (0 skipped).

## Area 3 — Critical business journeys ✅ CLOSED

**Baseline under test:** `b085030` (RC Area 2 close). **Date:** 2026-09-09.
Scope: end-to-end verification of the nine named business journeys (Student lifecycle, Finance, M-Pesa, Attendance, Assessments, Staff & Leave, Notifications, Documents, Reporting) across the frozen backend — not re-testing individual services in isolation. Grounded via direct reading of every service/API file involved before proposing any fix (`admissions/services.py`, `assessments/services.py` all five transition functions, `students/services.py` + `api.py` in full, `reporting/catalogue.py` in full, `CELERY_BEAT_SCHEDULE`, plus a repo-wide grep for the `_require_campus_scope` convention). Full scoping plan reviewed and approved in two rounds before any code was written.

### Findings

| Journey | Finding | Classification | Resolution |
|---|---|---|---|
| 1. Admission → Student → Placement | `enroll_application()` created a `Student` but never a `StudentEnrollment`; no permission check; no API existed for Admissions at all — the declared journey had no reachable entry point | **BLOCKER, fixed this area** | New `POST /api/v1/admissions/applications/{id}/enroll/`, gated on `admissions.enroll` (+ `academics.students.enroll` for the placement half). `enroll_application()` rewritten as one `@transaction.atomic` orchestration: locks the `Application` (`select_for_update`), requires `ACCEPTED` status, validates campus consistency across the application/class/actor-membership *before* any write, creates `Student` + `StudentEnrollment` in one transaction (reusing `academics.services.enroll_student`'s own validation), then transitions `Application → ENROLLED`. Concurrent double-enrollment: the lock serializes the two requests: the loser re-reads `status == ENROLLED` and gets a clean `400 ValidationError`, zero extra rows. Verified live under PostgreSQL 17 (`apps/admissions/test_concurrency.py`). |
| 5. Assessments approval chain | `_require_subject_class_authorization` (full `TeacherAssignment` scope) was enforced for create/mark-entry, but **no campus scope at all** was enforced for submit/reject/approve/reopen/publish — confirmed not mitigated elsewhere (`AssessmentDetailView.get_queryset` filters by tenant only) | **DEFECT, fixed this area** | New `_require_assessment_campus_scope(*, membership, assessment)` helper (same semantics as `reporting.catalogue.require_campus_scope`), called from `reject_assessment_submission`, `approve_assessment`, `reopen_approved_assessment`, `publish_assessment`. `submit_assessment_for_approval` was left unchanged — it already has the full `_require_subject_class_authorization` check. |
| 8. Documents / Students | `apps.students`'s entire real surface (list, document list/create/download/delete) filtered by **tenant only** — the one app that owns `Student.campus` was the one domain in the codebase not enforcing it, unlike every downstream consumer (Attendance, Assessments, Staff, Leave, Timetable, Reporting) | **DEFECT, fixed this area** | Query-time campus scoping added to `StudentListView.get_queryset` and the student/document lookups backing document list/create/download/delete (cross-campus resources 404, preserving anti-enumeration behaviour), plus a matching service-layer `_require_campus_scope` check in `add_student_document`/`delete_student_document`. `change_student_status`/`place_student` deliberately left unchanged — confirmed to have zero permission checks and zero callers outside tests (no API), so adding campus scope there would mean adding new authorization, not fixing a demonstrated gap. |
| 9. Reporting authorization | `assessments.results_sheet` had no `authorize` hook — a tenant-wide `reports.assessments.export` holder could pull any class's results, bypassing the scope the same data enforces via direct access | **DEFECT, fixed this area** | New `require_assessment_class_campus_scope` in `reporting/catalogue.py`, same campus-scope semantics as the Assessments fix above, wired as `authorize=` on the `assessments.results_sheet` catalogue entry. |
| 9. Reporting audit trail | No audit trail for report export request or download — only report *generation* is otherwise observable | **DEFECT, fixed this area** | `report.export.requested` recorded in `request_report_export` (all success paths: fresh job, replay, race-then-replay) and `report.export.downloaded` recorded in `ReportExportDownloadView`, only after authorization and document-availability both succeed. Bounded, non-sensitive metadata only (`job_id`, `report_code`, `document_id`, `row_count`) — never raw report parameters. Denied download attempts are deliberately not audited here (a future security-monitoring concern, not part of this fix). |
| 3. M-Pesa callback boundary | No HTTP-level test proved the public, unauthenticated webhook endpoints never create financial records directly | **RC VERIFICATION GAP, closed this area** | Added `test_duplicate_webhook_deliveries_never_create_financial_records_on_their_own` (`apps/finance/mpesa_api_tests.py`): two webhook deliveries with the same `TransID` produce two `MpesaCallbackLog` rows and **zero** `IncomingPayment`/`Payment`/`Receipt` rows — proving the verify → process boundary cannot be bypassed by the public endpoint alone, before the existing authenticated verify/process pipeline ever runs. Test passed on first write — no code change required. |
| 6. Leave cancellation | No concurrency test existed for `cancel_approved_leave_request`, despite the function correctly locking the `LeaveRequest`/`Employee` rows | **RC VERIFICATION GAP, closed this area** | Added `test_competing_cancellations_of_the_same_request_serialize_and_reverse_exactly_once` (`apps/leave/test_concurrency.py`), mirroring the existing approval-race pattern. Passed on first write under PostgreSQL 17 — confirms the existing locking was already correct. |
| 8. Orphaned-document reconciliation | `find_orphaned_storage_keys` had no minimum-age guard (a file could be flagged the instant it's written, before its owning transaction commits), and was only reachable via a manual management command — no Celery task existed | **Operational hardening, done this area** | `list_keys`/`find_orphaned_storage_keys` now take `min_age_seconds` (default 1 hour); new `purge_orphaned_documents()` service function shared by the management command and a new `purge_orphaned_documents_task` (`apps/documents/tasks.py`), scheduled daily via `CELERY_BEAT_SCHEDULE`. |

### Go-live acceptance decisions requiring sign-off

These are business/product-readiness questions, not code defects — carried forward to Area 8 for explicit sign-off, not buried as ordinary accepted risks.

| Decision | Detail |
|---|---|
| **SMS/Email delivery** | Configured SMS and Email notification channels are stub-only — they do not send to a real external provider. `IN_APP` delivery is real. If real external delivery is a declared launch requirement, provider integration is required before go-live; otherwise this ships as a known, accepted limitation. |
| **Finance organizational scope** | Finance (`fee_statement`, `collections_summary` reports; and the Finance domain generally) has no campus dimension — it is tenant-wide by design today, with no evidence that's wrong. Open question: are finance/bursar users ever campus-restricted in practice? If yes, the fix belongs in Finance's own authorization model (an Area 4 concern), not a Reporting-only patch — patching Reporting alone ahead of this decision would create a false sense of scoping while the underlying Finance APIs stayed tenant-wide, so no such patch was made this area. |

### Accepted risks (ordinary, recorded, no code change)

- **M-Pesa webhook auth** relies on a URL-embedded callback token plus mandatory human verification before any financial record is created — no HMAC signature or IP allowlist. Compensating control: `process_mpesa_callback()` hard-requires the permissioned `verify_mpesa_callback()` attestation, so a forged callback alone cannot manufacture a financial record (now also proven at the HTTP layer — see above). Stronger provider-side auth is backlog.
- **Assessment approval chain** has no software-enforced segregation of duties between submit/approve/publish — organizations can segregate these responsibilities through role configuration, but the backend does not enforce that the three actors must be distinct users.

### Not a defect (recorded so they aren't re-litigated)

Finance: `InvoiceStatus.VOID` unused (`issue_credit_note` is the documented correction path); no campus scoping in Finance (deliberate, no `Campus` FK on any Finance model); reversals audited via `ActivityEvent` (consistent with Milestone 22.2 precedent). M-Pesa: human-attested verification is deliberate; duplicate `MpesaCallbackLog` rows on retry are harmless; ack-before-process design is deliberate and already tested. Attendance: post-submit session edits and the shared mark/submit permission are both deliberate, documented design. Assessments: correction-audit scoping is correct as-is; "Milestone 7.1" naming is a documentation nit, not a defect. Leave: unlocked draft edits are low-stakes, last-write-wins is acceptable pre-submission. Notifications: manual-only retry is a deliberate dead-letter/human-review pattern; the single `invite_user` notification-bypass caller is unchanged existing precedent. Documents: the Documents-primitives-don't-self-audit design is intentional (Students/Staff audit at their own layer).

### Post-go-live (backlog, no action this area)

Journey 1 enrollment capacity/date-window validation; Journey 2 formal credit-note reversal/self-service workflow (an unimplemented capability with an existing manual-escalation workaround, not a conscious risk); Journey 6 no notification on leave cancel/withdraw; Journey 7 no delivery-confirmation webhook (`DELIVERED` status modeled but never set).

### Journey evidence matrix

| Journey | E2E | Tenant/campus scope | Idempotency/concurrency | Failure/retry | Result |
|---|---|---|---|---|---|
| 1. Admission → Student → Placement | Fixed this area (new endpoint) | Fixed this area | PostgreSQL-verified (`apps/admissions/test_concurrency.py`) | Locked/replay-safe by design | ✅ |
| 2. Finance lifecycle | Pre-existing, re-verified | Deliberate no campus scope | PostgreSQL-verified (`finance/test_concurrency.py`, 11 tests) | Pre-existing, re-verified | ✅ |
| 3. M-Pesa callback → payment | Pre-existing, re-verified | N/A | New HTTP-boundary test + existing service-level suites (`test_mpesa_concurrency.py`, `test_mpesa_recovery.py`) | Pre-existing, re-verified | ✅ |
| 4. Attendance | Pre-existing, re-verified | N/A (no campus concept in this journey) | PostgreSQL-verified (`attendance/test_concurrency.py`) | Pre-existing, re-verified | ✅ |
| 5. Assessments | Pre-existing, re-verified | Fixed this area (4 transition functions) | PostgreSQL-verified (`assessments/test_concurrency.py`) | Pre-existing, re-verified | ✅ |
| 6. Staff & Leave | Pre-existing, re-verified | Pre-existing | New cancellation-race test added, PostgreSQL-verified | Pre-existing, re-verified | ✅ |
| 7. Notifications | Pre-existing, re-verified | N/A | PostgreSQL-verified (`notifications/test_concurrency.py`) | Manual retry (accepted design) | ✅ (see go-live decision on SMS/Email) |
| 8. Documents / Students | Fixed this area (Students campus scope; orphan-purge hardened) | Fixed this area | PostgreSQL-verified (`documents/test_concurrency.py`) | Pre-existing, re-verified | ✅ |
| 9. Reporting | Fixed this area (results-sheet scope; audit trail) | Fixed this area | PostgreSQL-verified (`reporting/test_concurrency.py`) | Pre-existing, re-verified | ✅ (see go-live decision on finance scope) |

### Full test suite (SQLite + PostgreSQL 17)
SQLite: 822 tests, OK (44 skipped — Postgres-only cases). PostgreSQL 17 (`postgres:17`, disposable container `school-rc-area3-pg`): 822 tests, OK (0 skipped), including every existing Postgres-only concurrency suite re-run as this area's evidence and the two new concurrency tests (Journey 1 enrollment race, Journey 6 cancellation race). No schema changes — `makemigrations --check --dry-run` clean.

## Area 4 — Finance & M-Pesa integrity ✅ CLOSED

**Baseline under test:** `8ad5a13` (RC Area 3 close). **Date:** 2026-09-09.
Scope: auditing (not redesigning) the existing financial controls — ledger/accounting invariants, invoice/credit-note integrity, payment and allocation invariants, reversal correctness, incoming-payment reconciliation, M-Pesa STK/callback verification and processing, idempotency/replay protection, concurrency behavior, tenant isolation, permission enforcement, and auditability. Grounded via direct reading of `issue_credit_note`, `_invoice_outstanding_balance`, `allocate_payment`, `assign_fee_structure`, `generate_invoice`, and `FinanceSetupView` before classifying two open questions, plus a full pass over `mpesa_services.py`/`mpesa_api.py` and every existing Finance/M-Pesa test file.

This is a mature, heavily-tested subsystem: 11 pre-existing Postgres concurrency tests in `finance/test_concurrency.py` plus ~15 across `test_mpesa_concurrency.py`/`test_mpesa_recovery.py`. No BLOCKER was found. The M-Pesa pipeline in particular (durable-intent-before-external-I/O on STK initiation, a hard verify→process boundary, row-locked idempotent replay at every layer) required no code changes at all beyond what RC Area 3 already closed.

### Findings

| Finding | Classification | Resolution |
|---|---|---|
| `generate_invoice`'s `IntegrityError` catch re-labeled *any* integrity failure as "conflicted with another request," unlike `record_payment`/`ingest_incoming_payment`, which check the specific constraint name before translating | **DEFECT, fixed this area** | Catch now checks for `unique_invoice_idempotency_per_tenant` (or the SQLite-equivalent message) specifically, re-raising unmasked otherwise, and performs the same existing-row replay lookup `record_payment` already does. Verified with a real forced invoice-number collision between two different assignments (`apps/finance/tests.py::test_unrelated_integrity_error_during_invoice_generation_is_not_masked_as_idempotency_replay`), mirroring the identical existing test for `record_payment`. |
| Several money-adjacent Finance actions had no audit trail: `approve_fee_structure` (the gate that makes a fee structure usable for real invoicing), `generate_invoice` (only the later, separate `issue_invoice` call was audited — a generated-but-never-issued invoice left zero trail), and `FinanceSetupView.update` (tenant currency/fiscal-year/configuration changes bypassed `services.py` and had no audit call at all) | **DEFECT, fixed this area** | Added `record_activity` calls: `fee_structure.approved`, `invoice.generated` (idempotent replay of an existing invoice correctly records no additional event), and `finance_setup.updated` — the last with bounded metadata (`changed_fields` only, never the raw `configuration` JSON payload, which is arbitrary tenant-supplied data). Directly analogous to the Area 3 Reporting audit-trail fix. |
| `assign_fee_structure` has no `@transaction.atomic`/`select_for_update` of its own, unlike every other write path in this file — relies entirely on Django's internal `get_or_create` retry-once behavior | **RC VERIFICATION GAP, closed this area** | Added a PostgreSQL concurrency test (`FeeAssignmentConcurrencyTests`) proving two concurrent assignment attempts for the same `(student, fee_structure)` pair collapse to exactly one row with no unhandled exception. Passed — confirmed correct behavior, no code change required. |
| Credit notes are bounded only by invoice face value (`amount > invoice.total - issued_credits`), never netted against existing payment allocations — untested in the reverse order (credit note issued *after* an invoice is already fully allocated) | **RC VERIFICATION GAP, closed this area** | Investigated the consequence before writing the test: the ledger is live-summed and append-only (no cached/stored balance to drift), so this cannot corrupt the student's aggregate balance — it only drives that one invoice's own outstanding balance negative, cleanly blocking further allocation to it. Added a test proving exactly this (`test_credit_note_issued_after_full_allocation_blocks_further_allocation_without_corrupting_balance`) — confirmed correct, standard accounts-receivable behavior, no code change required. |
| No automated timeout/expiry for stuck `PENDING`/`UNKNOWN` M-Pesa STK requests (no callback ever received) | **ACCEPTED RISK** | Recovery is entirely human-invoked today (`query_stk_request`, `identify_stk_request`), both already correct and tested; stuck requests remain visible via `StkRequestListView` for manual reconciliation indefinitely. An operational dashboard/alert on aged requests is a reasonable post-go-live monitoring enhancement, not a financial-integrity defect. No code change. |

### Carried-forward items, explicitly re-evaluated (not silently accepted)

- **Finance organizational/campus scope** — re-confirmed this area: a repo-wide grep for "campus" inside `apps/finance/` still returns zero matches, and `require_permission` resolves membership by `(user, tenant)` only, never `membership.campus_id`. No new evidence changes the underlying business question. **Unchanged — remains a GO-LIVE ACCEPTANCE DECISION**, carried forward verbatim from Area 3 (see below).
- **Credit-note reversal/void workflow** — re-confirmed: `CreditNoteStatus.VOID`/`InvoiceStatus.VOID` remain defined but genuinely never set by any service function; no `void_credit_note` capability or route exists anywhere. **Unchanged — remains POST-GO-LIVE.**

### Go-live acceptance decisions requiring sign-off

| Decision | Detail |
|---|---|
| **Finance organizational scope** (carried from Area 3) | Are finance/bursar users ever campus-restricted in practice, or is finance always a tenant-wide role? If campus-restricted bursars are a real scenario, the fix belongs in Finance's own authorization model, not a Reporting-only patch. No evidence found this area that changes the answer either way — this needs a business decision, not further code investigation. |
| **Fee-structure-creation / payment-recording frontend operator journeys** | The frontend already flags both as blocked (`frontend/src/features/finance.tsx`: "New structure" and "Record payment" buttons disabled with tooltips). Confirmed as genuine, narrow backend API gaps, not frontend-fixable alone: `apps.academics` has zero API surface at all (no way to list `AcademicYear`/`AcademicLevel` for a picker), and `PaymentMethod` has no list endpoint either — in both cases the *write* side already accepts the needed UUIDs. Open question: are these two operator journeys required to be available via the frontend at go-live? If yes, this is a small, well-scoped follow-up (two new read-only list endpoints, no write-side changes needed). If no, it moves to a future frontend/API milestone. No endpoint was built this area — this needs a business answer first, not a speculative API addition. |

> **Resolved outside Area 4 (post-`4b6d3d6`):** answered yes — both operator journeys are required. `GET /api/v1/academics/academic-years/`, `GET /api/v1/academics/academic-levels/`, and `GET /api/v1/finance/payment-methods/` were added (new `academics.setup.view` permission, gated the same way as every other list endpoint in this codebase) and the two frontend dialogs wired to them, in a separate commit after Area 4 closed. Area 4's own commit and findings above are left unamended as the historical record of what that area actually concluded.

### Accepted risks (ordinary, recorded, no code change)

- No automated alerting/expiry for stuck `PENDING`/`UNKNOWN` M-Pesa STK requests (see Findings above) — manual recovery path exists and is proven correct.

### Not a defect (recorded so they aren't re-litigated)

`MpesaCallbackLog` has no unique constraint (duplicate log rows on retry are harmless — real idempotency is enforced downstream at `MpesaStkPushRequest`/`IncomingPayment`); `PaymentAllocation`/`AllocationReversal` have no DB-level check constraint capping their sums against invoice total/allocation amount (enforced correctly in application code via row-locking in `allocate_payment`/`reverse_allocation`/`reverse_payment`, and no code path bypasses the service layer to write these rows directly); the ledger is live-summed rather than a cached/stored balance, so no drift is possible by construction; M-Pesa's durable-intent-before-external-I/O design and hard verify→process boundary (already proven correct by an extensive pre-existing test suite, reconfirmed this area); `InvoiceStatus.VOID` and other unused enum values besides credit-note `VOID` (the correction path via `issue_credit_note` remains the documented mechanism, carried from Area 3).

### Post-go-live (backlog, unchanged from Area 3)

Formal credit-note/invoice void/reversal workflow — an unimplemented capability with an existing manual-escalation workaround (a bursar can issue a new correcting credit note; there is no in-system "undo"), not a conscious risk being shipped.

### Full test suite (SQLite + PostgreSQL 17)
SQLite: 830 tests, OK (45 skipped — Postgres-only cases, including the one new `FeeAssignmentConcurrencyTests` test). PostgreSQL 17 (`postgres:17`, disposable container `school-rc-area4-pg`): 830 tests, OK (0 skipped), including a full re-run of every existing Finance/M-Pesa concurrency and recovery suite as this area's evidence. No schema changes — `makemigrations --check --dry-run` clean.

## Area 5 — Concurrency & failure recovery ✅ CLOSED

**Baseline under test:** `80503dd` (Staff campus-scope defect fix, applied ahead of this area — see Findings below). **Date:** 2026-09-11.
Scope: proving failure-recovery behavior that was previously established by design/code-reading but never verified under an actual induced failure, through the real `docker compose` stack (not simulated in Python). Four checks, run one at a time with recorded evidence.

### Findings (pre-Area-5, applied before this area's own checks)

| Finding | Classification | Resolution |
|---|---|---|
| A "candidate Area 5 finding" queued in project memory (Staff employee document list/download skipping the campus check upload/delete apply) turned out broader on direct code reading: `EmployeeListCreateView`, `EmployeeDetailView.get`, and `EmployeeQualificationListCreateView` were *also* unscoped, not just the two document endpoints originally recorded | **DEFECT, fixed ahead of this area (`80503dd`)** | Same shape as the Area 3 Students fix. New `resolve_staff_membership`/`campus_scoped` (mirroring `apps.students.api`'s pattern) applied to all five read paths; cross-campus access now 404s. Reclassified out of Area 5 proper — this is an authorization/data-scope defect, not concurrency/failure-recovery, and is recorded here only because it was queued under Area 5's name before this area started. |

### Check 1 — Redis/broker-loss proof for financial writes ✅ DONE

**Claim under test**: PostgreSQL is financial truth; losing Redis must never lose or block a confirmed Payment/M-Pesa/Invoice/Allocation write (standing rule, generalizes the Milestone 22.3 fail-open `/readyz/` design). Previously true by design (a grep of `apps/finance/` found no `@shared_task`/`.delay()`/`apply_async()` anywhere in the request-handling path — the only Celery task there is the hourly `resweep_unmatched_incoming_payments` reconciliation *helper*, not the primary write path) but never proven under an actual outage.

**Method**: against the live `docker compose` stack —
1. Recorded a baseline `IncomingPayment` (`POST /api/v1/finance/incoming-payments/`, `ingest_incoming_payment`) with Redis up. `201`, persisted.
2. `docker compose stop redis` — Redis container fully stopped, not just paused.
3. Repeated the same write (different reference) while Redis was down. `201`, persisted.
4. Also checked `/readyz/` (still `200`, `status: ok` — this dev compose config has no `DJANGO_CACHE_URL` set, so `cache: not configured`; see caveat below) and `POST /api/v1/auth/login/` (still `200`, so the login throttle's cache touch didn't block on a dead Redis either).
5. Queried PostgreSQL directly (`manage.py shell`, not the API) and confirmed both `IncomingPayment` rows exist with the expected `external_reference`/`status=UNMATCHED`.
6. `docker compose start redis` — Celery worker reconnected on its own (`Connected to redis://redis:6379/0`, then correctly processed its next scheduled beat tasks) with no manual restart needed.

**Result**: PASS. A financial write (M-Pesa/incoming-payment ingest) completes and persists to PostgreSQL with the Celery broker completely down, and the worker recovers automatically once Redis returns.

**Caveat, recorded honestly**: this dev `docker-compose.yml` doesn't set `DJANGO_CACHE_URL`, so `/readyz/`'s "cache: not configured" here is a different code path than the `FailOpenRedisCache` behavior Area 1 already proved under an actual Redis-backed cache. This check's real claim is narrower and specifically about the *Celery broker* not being on the financial-write critical path — confirmed both statically (grep) and dynamically (write succeeded with the broker fully down). Proving `FailOpenRedisCache` itself under this exact stack (a production-like config with `DJANGO_CACHE_URL` actually pointed at Redis) remains covered by Area 1's existing evidence, not repeated here.

### Check 2 — Real worker-crash lease reclaim ✅ DONE

**Claim under test**: if a Celery worker is killed abruptly after claiming a piece of durable work but before finishing it, no work is lost, the lease doesn't stay stuck forever, the real `reap_stale`/Beat schedule reclaims it, and it's reprocessed exactly once — with no manually edited PostgreSQL rows anywhere in the sequence.

**Method** (real process kill, not a simulated exception — against the live `docker compose` stack):
1. `docker compose stop celery-worker` (celery-beat stays running throughout, so its schedule keeps ticking on real wall-clock time) — done so the real worker can't race the manual claim below.
2. Created a genuine `NotificationOutbox` row via a real business action: `POST /api/v1/tenancy/users/invite/` (demo-academy tenant). Confirmed `PENDING`, `attempts=0`, no lease.
3. Ran a driver process inside the backend container that calls the actual `apps.activity.durable_work.claim_due()` primitive directly (`lease_seconds=10`, otherwise identical to what `dispatch_pending_notifications` itself calls), then blocks (`time.sleep(600)`) — standing in for a worker that has claimed the row and is now mid-task. Confirmed via a separate query: `PROCESSING`, `attempts=1`, real `lease_expires_at` committed to PostgreSQL.
4. Found the driver process by scanning `/proc/*/cmdline` **inside the container's own PID namespace** (its host-visible PID from `docker top`, 2726, was confirmed to differ from its container-internal PID, 170 — a real illustration of why "kill by host PID" doesn't work here) and sent it a real `SIGKILL` (`os.kill(170, signal.SIGKILL)`). Confirmed gone from `docker top` immediately after.
5. Re-queried the row immediately post-kill: **still `PROCESSING`, lease already expired, nothing has reclaimed it yet** — PostgreSQL genuinely retained the claimed-work state exactly as the crashed process left it.
6. `docker compose start celery-worker` (the "new worker").
7. Polled the row's real status every 5s (no manual intervention) for up to 380s:

   | Time | Status | Attempts | Lease |
   |---|---|---|---|
   | 11:23:08 | `PROCESSING` (unchanged) | 1 | `08:21:57` (already expired) |
   | 11:24:13 | `PENDING` | 1 | `None` — reclaimed by the real, Beat-scheduled `reap_stale_notifications` task |
   | 11:24:38 | `PROCESSED` | 2 | `None` — reclaimed and reprocessed by the real, Beat-scheduled `dispatch_pending_notifications` task |

8. Checked `NotificationDeliveryAttempt` for this outbox row: **exactly one** row (`attempt_number=2`, `status=SENT`, `provider=EMAIL`) — the crashed claim (`attempt_number` would have been 1) never got far enough to create a delivery attempt at all, so there is no duplicate and no dangling attempt row.

**Result**: PASS, matching the full expected sequence —

```
PENDING → worker claims → PROCESSING+lease → WORKER KILLED (real SIGKILL)
   → lease expires → real Beat-scheduled reaper reclaims → PENDING
   → real worker claims → PROCESSING → PROCESSED (attempts=2, 1 delivery attempt)
```

No lost work (same row id throughout), no permanently stuck lease (reclaimed automatically, no manual DB edit), no duplicate business side effect (1 delivery attempt, not 2), bounded retry (`attempts=2`, well under `MAX_ATTEMPTS=5`), and full recovery driven entirely by the existing scheduled tasks — total wall-clock time from kill to `PROCESSED` was under 2 minutes (`reap_stale_notifications`'s 300s Beat interval happened to be partway through its cycle, not a worst-case 5-minute wait).

No defect found; `apps/activity/durable_work.py` required no changes.

### Check 3 — Redelivery idempotency, proven not just reasoned ✅ DONE

**Claim under test**: `CELERY_TASK_ACKS_LATE=True` + `CELERY_TASK_REJECT_ON_WORKER_LOST=True` (Milestone 22.3) means a crashed worker's in-flight task message is redelivered to another worker — reasoned as safe "by construction" at the time (`select_for_update(skip_locked=True)`), never proven by actually triggering two overlapping executions of the same real task.

**Method**: for these periodic sweep-style tasks, Celery-level redelivery would mean the *entire task function* (not a specific claimed row) runs again — so the faithful way to prove redelivery-safety is to run the real, registered task function itself concurrently from two workers, not just `claim_due()` in isolation (already exercised by Check 2).
1. `docker compose stop celery-worker` (same reasoning as Check 2 — keeps the real scheduled consumer from claiming the row before the deliberate concurrent test).
2. Created one fresh, genuine `NotificationOutbox` row via a real invite. Confirmed it was the *only* `PENDING` row in the table before the test (so the result is unambiguous).
3. Launched two independent OS processes in the backend container, started within 2ms of each other, each calling the real `apps.notifications.tasks.dispatch_pending_notifications()` — the literal function Celery invokes, not a reimplementation — simulating the exact scenario where Celery redelivers the same task to two workers simultaneously.
4. Process A's log shows the stub EMAIL gateway call (`EMAIL (stub): to rc-area5-check3@demo-academy.test`); process B's log shows no gateway call at all — `claim_due()`'s `select_for_update(skip_locked=True)` gave B an empty result set (the row was already locked by A), so B did nothing and exited cleanly with no error.
5. Confirmed final state: `status=PROCESSED`, `attempts=1`, and **exactly one** `NotificationDeliveryAttempt` row (`SENT`).

**Result**: PASS. Two genuinely concurrent real invocations of the actual task function produced exactly one delivery, zero duplicates, zero errors — not just "the code looks idempotent" but a real concurrent race decided correctly at the PostgreSQL row-lock level. No defect found; no code changes required.

### Check 4 — Mid-transaction DB-connection-drop recovery ✅ DONE

**Claim under test**: if a task's database connection is lost while a claiming transaction is open (between the `SELECT ... FOR UPDATE` and the following `UPDATE`, the two statements `claim_due()` wraps in one `transaction.atomic()`), the row must not end up in a stuck or partially-applied state — PostgreSQL's own atomicity should roll the whole thing back cleanly, leaving the row exactly as if the claim never happened.

**Method**: a real, externally-triggered connection kill via PostgreSQL's own `pg_terminate_backend()` — not a Python exception, not a manual row edit — against the live `docker compose` stack.
1. `docker compose stop celery-worker` (same race-avoidance as Checks 2-3). Created one fresh, genuine `NotificationOutbox` row via a real invite. Confirmed `PENDING`, `attempts=0`.
2. Ran a driver process reproducing `claim_due()`'s exact real ORM operations (same `select_for_update()`, same `transaction.atomic()` wrapper, same model) via `manage.py shell`, with a 30s `time.sleep()` inserted between the `SELECT FOR UPDATE` and the `UPDATE` — the only place a manual pause was added, and only in this disposable driver script, never in `apps/activity/durable_work.py` itself. Captured its real PostgreSQL backend PID (`SELECT pg_backend_pid()`): `733`.
3. From a **separate** connection, ran `SELECT pg_terminate_backend(733)` — a real administrative kill of that exact backend process while its transaction was genuinely open and uncommitted. Result: `True`.
4. The driver's subsequent `UPDATE` attempt (after its sleep elapsed) failed exactly as expected: `django.db.utils.OperationalError: terminating connection due to administrator command` (via `psycopg.errors.AdminShutdown`). The `"UPDATE REACHED"` line was never printed — the write never happened.
5. Re-queried the row: **`PENDING`, `attempts=0`, no lease — byte-for-byte identical to its pre-transaction state.** PostgreSQL's rollback discarded the entire open transaction, including the `SELECT FOR UPDATE`'s row lock; no reap_stale action was even needed, because the row was never marked `PROCESSING` in the first place.
6. Restarted `celery-worker` and let the real, unmodified `dispatch_pending_notifications` schedule pick the row up on its own: processed to `PROCESSED` on the very next 30s cycle, with no manual intervention.

**Result**: PASS. A connection dropped mid-transaction produces zero partial state — not a stuck lease, not a phantom claim, nothing for `reap_stale` to even need to clean up — and the row recovers to normal processing automatically on the next real dispatch cycle. No defect found; `apps/activity/durable_work.py` required no changes.

### Area 5 summary

All four checks PASS with no code changes required. `apps/activity/durable_work.py`'s claim/lease/reap design holds under three distinct real failure injections (Celery broker down, abrupt `SIGKILL` of a worker holding a claim, concurrent redelivery-style double-invocation, and a mid-transaction PostgreSQL connection kill), each proven against the live `docker compose` stack rather than reasoned from code alone.

## Area 6 — Performance & capacity ✅ CLOSED

**Baseline under test:** `bddebb9` (LoginPage error-signal fix, applied ahead of this area — unrelated frontend defect found live while Area 6 tooling ran). **Date started:** 2026-09-11.
Scope: Milestone 5E's existing load-test tooling (`docs/architecture/load-testing.md`, `backend/loadtest/`) measures throughput/capacity against the real `docker compose` + `docker-compose.loadtest.yml` stack (gunicorn, explicit Celery concurrency, real PostgreSQL 17/Redis). Establish baselines first; only change code where measured evidence identifies an RC-level defect. Run at full documented ramp scale (100/500/1000/2500/5000 events/min, 300s/step).

### Pre-check: two real defects found and fixed in the load-test tooling itself, before any capacity measurement (each its own commit, per RC process)

| Finding | Classification | Resolution |
|---|---|---|
| `run_ramp`'s outer loop visited every tenant once per pass regardless of weight — `weight` only changed the sleep between sends, not how many times a tenant was actually hit, so noisy-neighbor runs silently produced ~1:1 tenant traffic instead of the intended 10:1 | **DEFECT, fixed** (`062475e`) | Each tenant now runs its own independent pacing loop for the full step, firing at its own weighted rate concurrently — mirrors the pattern already used by `run_operator_pool`/`run_stk_trickle`. Verified live at full ramp scale in 5E-1 below: measured ratio **9.17:1** (target 10:1) across 44,117 real events. |
| Harness sent `httpx.BasicAuth` for every authenticated call (operator pool, STK trickle, 5e2 processing, backlog drain) — `config.settings.DEFAULT_AUTHENTICATION_CLASSES` is `[TokenAuthentication, SessionAuthentication]` (Milestone 22.4), no `BasicAuthentication`, so every such request 401'd. `drain_backlog`'s old "non-200 counts as zero" handling masked this as a clean "backlog drained" — `loadtest_reconcile` then correctly caught it as 28/28 `missing_payment` | **DEFECT, fixed** (`1d2e028`) | New `_tenant_headers()`: logs each bursar in once via the real login endpoint, caches the token. `drain_backlog` no longer treats a non-200/error as a confirmed zero. Verified live: re-ran the smoke phase — every request 200/201, `loadtest_reconcile` PASS (was 28/28 `missing_payment`). |

### 5E-1 — Callback-ingestion capacity, as-configured ✅ MEASURED

**Run:** `area6-5e1`, 20 tenants (1 noisy at 10x student count), full ramp `100,500,1000,2500,5000` events/min × 300s/step (~28 min incl. drain), server-side sampler at 5s intervals for the full window.

**Noisy-neighbor weighting, proven at real scale (non-negotiable per scope)**: 44,117 total events. Noisy tenant (`loadtest-0`): **14,363** requests. Every one of the other 19 tenants: **exactly 1,566** each. Measured ratio **9.17:1** against a target of 10:1 (weights 10 vs 1 of 29 total) — the small gap from real async-scheduling jitter over a 28-minute run, not a repeat of the fixed bug. This is the harness's own weighted traffic, not simulated.

**Headline finding — real 429 ceiling, not a deeper bottleneck**: 82.81% error rate overall, **100% of it `429 Too Many Requests`**, starting at the 500 events/min step:

| Ramp step (events/min, combined) | 200 | 429 |
|---|---|---|
| 100 | 514 | 0 |
| 500 | 961 | 1,421 |
| 1000 | 1,706 | 3,044 |
| 2500 | 1,962 | 9,311 |
| 5000 | 2,286 | 19,372 (final partial step: 153 / 3,373) |

Root cause, confirmed by direct code reading: `apps/finance/mpesa_api.py`'s three M-Pesa webhook views (`C2BConfirmationView`, `C2BValidationView`, STK callback) use stock DRF `ScopedRateThrottle` with `throttle_scope = "mpesa_callback"` (`THROTTLE_RATE_MPESA_CALLBACK`, default `120/min`) and **no custom `get_cache_key`** — meaning DRF's default anonymous-request keying applies: **by source IP, not by tenant/`callback_token`**. Server-side samples show PostgreSQL was essentially idle throughout (1 active connection the entire run, 0 deadlocks, 0 lock waits) — the throttle is the ceiling; nothing deeper was even reached.

**Classification: RC capacity-planning finding requiring a go-live acceptance decision, not a code defect fixed this area.** The throttle is deliberately documented as "a protective ceiling against a flood/DoS, not ordinary traffic shaping" (`config/settings.py`) and is working exactly as configured. But because it's IP-keyed rather than tenant-keyed, and Safaricom's Daraja C2B confirmations for **every tenant on the platform** originate from Safaricom's own infrastructure, this could mean **~120/min combined across the whole platform**, not per-school, if Safaricom's callback source IPs are a small/shared set in production (not verified against the real Daraja sandbox here — this tool always runs against `loadtest/fake_daraja.py`, never the real internet, per the tool's own design). Business/architecture decision needed: should `mpesa_callback` be re-scoped to key by `callback_token` (tenant-aware) instead of source IP, or is `120/min` platform-wide an acceptable ceiling for the expected go-live scale? **Not fixed this area** — reclassifying DRF's throttle keying is a real code change candidate, not a load-test-environment tuning knob (unlike the login-throttle override below, which is purely a test-harness artifact).

**Load-test-environment-only override, separate from the finding above**: also added `THROTTLE_RATE_LOGIN=1000/min` to `docker-compose.loadtest.yml` (not production) — ~20 tenants each logging in once from the harness's single client IP collided with the real 5/min-per-IP login throttle (RC Area 2), which is not the capacity dimension under test. This one *is* purely a test-tooling artifact: real users don't log in from one shared IP in bulk the way Safaricom's shared webhook source plausibly does.

**Next**: re-run 5E-1 (and subsequent phases) with `THROTTLE_RATE_MPESA_CALLBACK` raised for the load-test environment only, clearly labeled as an exploratory/throttle-disabled run, to find the *next* bottleneck layer (DB/Celery/connection pool) beneath the throttle — reported separately from the as-configured number above, never conflated with it.

Full per-phase/traffic-class latency table and per-5s server-side samples: `backend/loadtest/reports/area6-5e1.md` (generated, not reproduced in full here).

### 5E-1-explore — Callback-ingestion capacity, throttle raised ✅ MEASURED

**Run:** `area6-5e1-explore`, same 20-tenant manifest and full ramp (`100,500,1000,2500,5000` events/min × 300s/step) as the as-configured run above, but launched with `docker-compose.loadtest.throttle-explore.yml` (`THROTTLE_RATE_MPESA_CALLBACK=100000/min`, load-test environment only — `5f566f3`). Purpose: with the 120/min-per-IP throttle out of the way, find the *next* bottleneck layer (HTTP/DB/Celery) beneath it, per the plan recorded above. Clearly labeled exploratory — never to be conflated with the as-configured 120/min finding.

**Result: no deeper bottleneck found at the ramp levels tested.** 44,708 total requests, overall error rate **0.02%** (8 client-side timeouts/exceptions, zero non-2xx HTTP responses across the entire run):

| Ramp step (events/min) | n | HTTP errors | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|
| 100 | 514 | 0 | 63ms | 125ms | 1,485ms | 1,625ms |
| 500 | 2,381 | 0 (2 client-side) | 63ms | 172ms | 406ms | 1,890ms |
| 1000 | 4,796 | 0 (1 client-side) | 78ms | 156ms | 437ms | 1,125ms |
| 2500 | 11,947 | 0 (5 client-side) | 94ms | 219ms | 454ms | 3,656ms |
| 5000 | 25,070 | 0 | 109ms | 235ms | 625ms | 2,344ms |

The top step actually achieved ~83.6 req/s (25,070 requests / 300s), matching its 5,000/min target — the harness kept pace and the backend absorbed it. Latency grows mildly but stays well bounded even at the top step. Server-side samples: PostgreSQL stayed essentially idle throughout (1-2 active connections, 0 deadlocks, 0 lock waits) — confirming the ingestion HTTP view + `MpesaCallbackLog` insert is cheap and was never DB-bound, at any tested rate.

**Honest capacity statement (per scope — no invented target):** callback-ingestion capacity is *at least* ~83.6 req/s sustained for 5 minutes with negligible errors and bounded latency. The exploratory run did **not** locate the true ingestion-layer ceiling — it was never reached at the ramp levels tested. Establishing the actual ceiling would require pushing the ramp beyond 5,000 events/min, which is out of scope for this run (the documented ramp tops out at 5,000/min); recorded as a capacity-planning note, not fabricated as a number we didn't measure.

**Important scope note — the growing `MpesaCallbackLog` backlog is expected here, not a defect:** the sampler's `callback_backlog_count` climbed to 52,286 by the end of the run and did not drain, including ~2 minutes of idle sampling after the harness stopped sending. This is **by design for the 5e1 phase**, confirmed by direct code reading: `harness.py`'s `5e1` phase only runs `run_ramp` (raw C2B confirmations into the unverified inbox) and explicitly sets `expect_payment=False` — it never runs the operator pool that performs `CallbackVerifyView`/`CallbackProcessView` (`apps/finance/mpesa_api.py`), which are authenticated, evidence-required, human/bursar-driven actions per the frozen 5C.1 trust boundary (callback → durable unverified inbox → **verification** → **process** → `IncomingPayment` → settlement) — there is no automatic Celery task that drains this backlog, deliberately, since an unverified callback must never become money on its own. Verified financial-processing capacity is a distinct, not-yet-measured number — that's what 5E-2 (operator-pool-driven verify/process throughput) tests next, per Area 6 scope's "distinguish callback ingestion / verified financial-processing / sustainable mixed end-to-end" instruction.

`loadtest_reconcile` PASS — no violations, confirming the high-volume ingestion itself produced no duplicate/missing-payment integrity issues even before any verification occurred.

Full latency table and per-5s server-side samples: `backend/loadtest/reports/area6-5e1-explore.md` (generated, not reproduced in full here).

### Defect found during 5E-2 setup — throttle cache was per-worker, not shared ✅ FIXED

**Symptom:** a first 5E-2 attempt (`area6-5e2`, as-configured, full ramp) came back with **0.00% errors at every step**, despite each of the 20 bursar users issuing ~1,952 `/callbacks/{id}/process/` requests over the 25-minute run — far past the default `1000/hour` per-user throttle, which should have started `429`ing well before that.

**Root cause, confirmed directly (not inferred):** `settings.CACHES` in the running container was `django.core.cache.backends.locmem.LocMemCache` — Django's default, per-Python-process cache — and a live Redis scan during the run found **zero** throttle keys. `config/settings.py` only assigned `config.cache.FailOpenRedisCache` (the Redis-backed cache DRF's throttle counters live in) inside `if PRODUCTION:`. No compose file sets `DJANGO_ENV=production`, so this entire RC/load-test stack — which deliberately runs Gunicorn with 4 worker *processes* for a meaningful capacity number (`docker-compose.loadtest.yml`) — has been throttling against 4 independent, unshared counters the whole time. Every scope tested so far (`login`, `mpesa_callback`, and now the default `user` scope) was affected: each worker independently allows up to the full configured rate before its own local counter maxes out.

**Classification: DEFECT, fixed this area** (`9861d41`). Per the RC rule ("an actual defect gets its own fix + regression test + commit before Area 6 validation continues"), fixed before re-attempting 5E-2:
- `config/settings.py`: the Redis-cache assignment (and its URL-shape validation) now keys on `DJANGO_CACHE_URL` being set, not on `PRODUCTION`. Production is unaffected (`DJANGO_CACHE_URL` was already mandatory there via the `required` tuple). Plain local dev without Redis is unaffected (`CACHES` stays unset, Django's own default applies).
- `docker-compose.loadtest.yml`: now sets `DJANGO_CACHE_URL=redis://redis:6379/1` (distinct DB index from Celery's broker on `/0`) so RC/capacity runs measure real shared-cache throttle behavior going forward.
- Regression coverage: `test_non_production_with_cache_url_still_gets_the_shared_redis_cache` and `test_non_production_refuses_invalid_cache_urls_too` in `config/test_production_settings.py`. Full `config.test_production_settings` + `config.test_throttling` + `apps.tenancy.api_tests` + `apps.finance.mpesa_api_tests` (87 tests) verified green against the real Docker/Redis stack after the fix.

**Implication for already-recorded evidence (not reopening either section — see the Area 2 post-close note above):** every throttle ceiling measured against this stack before this fix — Area 2's live-Docker login-throttle collision (informal, not that area's primary evidence — its unit test is unaffected) and this area's own 5E-1 `mpesa_callback` finding (120/min, measured live) — was measured under the unfixed, effectively-multiplied condition. The true single-shared-cache ceiling for each is *at least as strict* as what was documented, likely stricter than what the flawed stack actually allowed through. 5E-1's headline classification ("RC capacity-planning finding requiring a go-live acceptance decision") still stands regardless — the fix doesn't change the architectural finding (IP-keyed, not tenant-keyed) — but the exact `82.81%`/step-by-step `429` numbers recorded there were measured pre-fix and are not re-verified against the corrected cache. Flagged for the user's awareness; an optional 5E-1 re-verification under the fix is available on request but not treated as blocking here, since the architectural finding (the thing requiring a go-live decision) doesn't depend on the exact pre-fix numbers.

### 5E-2 — Verified financial-processing capacity ✅ MEASURED

**Run:** `area6-5e2-fixed`, as-configured (real `120/min` mpesa_callback and `1000/hour` user throttles, real Redis-shared cache after the defect fix above). Setup: `loadtest_seed_verified_callbacks --count-per-tenant 500` (10,000 pre-verified `MpesaCallbackLog` rows across 20 tenants, created and verified through the real webhook + `verify_mpesa_callback()` service, *before* the timed window — so the window measures only `/callbacks/{id}/process/` throughput, never bypassing the verify prerequisite production traffic can't skip). Full ramp `100,500,1000,2500,5000` events/min × 300s/step, round-robin across all 20 tenants.

**Result:** 53,580 requests, overall error rate **33.49%** (35,638×`200`, 17,938×`429`, 4 client-side). Per-step breakdown:

| Ramp step (events/min) | n | 200 | 429 | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| 100 | 500 | 500 (100%) | 0 | 31ms | 78ms | 109ms |
| 500 | 2,347 | 2,347 (100%) | 0 | 16ms | 32ms | 62ms |
| 1000 | 4,441 | 4,441 (100%) | 0 | 16ms | 47ms | 93ms |
| 2500 | 13,194 | 13,194 (100%) | 0 | 31ms | 78ms | 156ms |
| 5000 | 33,098 | 15,156 (45.8%) | 17,938 | 125ms | 2,704ms | 3,531ms |

Per-tenant success counts are strikingly uniform: every one of the 20 users landed **1,776–1,786** successful `200`s (a ~10-request spread), confirmed via Redis `throttle_user_<uuid>` cache keys. `loadtest_reconcile`: **PASS**, no violations.

**Root-cause reading, since the number itself needs explaining (not just reported):** the configured `user` throttle is `1000/hour`, yet every user got ~1,780 successes — meaningfully more than the nominal cap, and steps 1–4 (cumulative ~1,024 requests/user by the end of step 4) show **zero** throttling at all despite already being past the nominal 1000. This is consistent with DRF's stock cache-based `SimpleRateThrottle` being non-atomic: `allow_request()` does a plain `cache.get()` (read history) then, after the view runs, `throttle_success()` does `cache.set()` (write history) — a classic read-then-write race with no locking or atomic increment. Under real concurrency (many in-flight requests for the same user across Gunicorn's worker processes/threads before any of them commits its write back), several requests can read the *same* stale history and all get admitted before the counter catches up — a known, documented characteristic of DRF's default throttle backend, not something specific to this codebase. The uniform per-tenant overshoot (all ~1,780, not scattered) matches this: round-robin traffic means every user experiences roughly the same concurrency profile, so they all overshoot by roughly the same amount.

**Classification: ACCEPTED RISK, not a code defect fixed this area.** The throttle is demonstrably *working* — it engaged, correctly per-user (not per-worker anymore), and rejected the majority of excess traffic once triggered (54.2% of the top step). It just isn't a mathematically exact ceiling under high concurrency, which is an inherent property of DRF's non-atomic counter, not a bug this codebase introduced. Building a fully atomic limiter (e.g. a Redis Lua script doing check-and-increment in one round trip) is a legitimate hardening option but is speculative optimization beyond what any *measured* evidence here requires fixing now — recorded as a **post-go-live** candidate if the business ever needs an exact rather than approximate per-user ceiling.

**Database:** stayed light throughout, even during the 33,098-request top step — max 4 active connections (16 total, pool headroom unused), **0 deadlocks, 0 lock waits, 0 conflicts**. `process_mpesa_callback` (which allocates a `NumberSeries`-backed receipt number per success) showed no contention signature at this volume — the throttle was the binding constraint, not the database.

**Honest capacity statement:** verified financial-processing capacity is *at least* ~44/s sustained (2500/min step, 100% success, p99 156ms) with zero DB contention. The true ceiling above that is throttle-shaped, not infrastructure-shaped, and — per the accepted-risk note above — the *effective* per-user ceiling under concurrent load is closer to ~1,780/hour than the configured 1,000/hour figure suggests.

### 5E-3 — Sustainable mixed end-to-end capacity ✅ MEASURED

**Scope:** a realistic mixed workload against all 20 tenants simultaneously — financial write path (C2B ramp → operator-pool verify/process), reconciliation polling (`callback_list`), STK-push initiation, and interactive Student/Attendance/Assessment reads — plus background Celery, plus a scripted, individually-attributable chaos sequence (`redis-outage` +400s/90s, `kill-worker` +800s/90s, `postgres-connection-kill` +1200s/15s), run for a 1500s steady-state step. Per the area's scope instruction, capacity is reported as three separate figures (normal / degraded-recovery / post-recovery), never blended into one TPS number.

#### Four real defects found and fixed in the load-test harness before a valid run was obtainable

Getting one clean, authoritative run took four iterations — each prior attempt's "result" turned out to be measuring a harness defect, not backend behavior, only visible once the *full* mixed workload ran at realistic sustained scale (none of these were catchable by a short smoke test):

| # | Finding | Classification | Resolution |
|---|---|---|---|
| 1 | `chaos.py` invoked `docker` via `subprocess.run(["docker", ...])`, relying on it being resolvable on `PATH` — the harness runs as a plain native-Windows process (not inside a container), and that process's `PATH` doesn't include Docker Desktop's install dir. The chaos thread crashed (`FileNotFoundError`) on its very first scenario, so an entire first "successful" run never actually injected any chaos | **Harness defect, fixed** (`16ec9cc`) | `_docker_executable()` resolves `docker` via `shutil.which()` with Docker Desktop path fallbacks; applied to every `subprocess.run` call site (one, in `kill_postgres_connections`, was missed initially and caught on a second live test). Verified live: all 3 scenarios + the full plan run correctly. |
| 2 | `run_operator_pool` cached one auth token per tenant and retried on *any* error (including `429`) with a flat 1s backoff — a self-sustaining throttle storm: 5 operators alone could out-poll the shared per-tenant budget before a single verify/process call was even counted, and retrying immediately on 429 consumed whatever budget regenerated almost as fast as it appeared, locking the tenant out for the rest of the run | **Harness defect, fixed** (`e96cf53`) | 20s backoff specifically on 429 (not a flat 1s), plus a 4s pacing floor on successful non-empty polls. Removed a wasteful duplicate GET found while investigating (the endpoint was being called twice per poll for no reason). |
| 3 | The same self-sustaining-storm pattern existed, unfixed, in `run_interactive_reads` and `run_stk_trickle` — only found because a *full* run (not a smoke test) showed these two traffic classes still at ~18-22% success while the operator-pool fix above had already brought `callback_list`/`verify`/`process` back to healthy | **Harness defect, fixed** (`fc99b5b`) | Same 20s-on-429 backoff applied to both. Verified via smoke test: 100%/96%/99.2% success, zero 429s across every traffic class — but see #4, this smoke test was too short to reveal the deeper issue. |
| 4 | **The real one.** All three traffic generators shared *one* login per tenant (`_tenant_headers`, cached by slug). DRF's `UserRateThrottle` is per authenticated user, not per tenant — `config/settings.py` documents this as a deliberate, accepted gap ("a tenant with 300 active users gets ~300x the throughput of a tenant with one"). At sustained full-scale load the shared account's `1000/hour` budget was always going to be exceeded by the *combined* demand of 5 operators + interactive reads + STK push — backoff pacing (#2, #3) only delayed the exhaustion, it couldn't prevent it. Once exhausted, `callback_list` collapsed to a **sustained 0% success** for the rest of a 25-minute run and never recovered | **Harness defect, fixed** (`910526c`) | `loadtest_provision.py` now provisions distinct accounts per traffic role (same permissions as bursar): 5 operator accounts (`--operator-accounts`, matching `--operators`), 1 registrar account for interactive reads, 1 frontoffice account for STK push — 140 new accounts across 20 tenants. `harness.py`'s `run_operator_pool` gives each concurrent operator its own login; `run_interactive_reads`/`run_stk_trickle` get their own dedicated accounts. `_tenant_headers` (single shared account) is kept for phases that don't need independent budgets (5e1/5e2 ramps, `drain_backlog`). |

Each fix was verified live (a real run or smoke test showing the specific symptom gone) before being trusted, per this area's evidence standard — not assumed correct from code reading alone.

#### Run: `area6-5e3-fixed4` — the first fully clean, authoritative measurement

20 tenants, `--ramp 100 --operators 5 --stk-rate 5 --interactive-rate 200`, 1500s steady-state + chaos plan + drain, real Redis-shared throttle cache, real gunicorn (4 workers × 2 threads). **63,143 metric samples, 2,338 C2B events** — roughly 2.7x the traffic of the prior (defect #4-contaminated) attempt, since the fix let real demand through instead of self-throttling it away.

*(Two earlier full runs against this same fixed harness — `area6-5e3-fixed3`, and an invalid run whose data was lost outright when an unrelated `docker compose up -d --build frontend` cascaded into recreating the `backend` container mid-run, killing it before it reached its own report-write step — are not cited further here; superseded by `fixed4`.)*

**Normal sustained mixed capacity** (the three undisturbed windows: before redis-outage, between redis-outage and kill-worker, between kill-worker and postgres-connection-kill — 960s combined, excluding any chaos-recovery buffer):

| Traffic family | req/min | Success | p50 | p95 | p99 |
|---|---|---|---|---|---|
| Financial critical path (`c2b_confirmation`/`callback_verify`/`callback_process`) | 455.2 | 99.2% | 110ms | 625ms | 2,484ms |
| Reconciliation polling (`callback_list`) | 2,136.9 | 99.8% | 31ms | 140ms | 515ms |
| STK push initiation | 87.4 | 99.7% | 93ms | 406ms | 15,032ms (one slow outlier in an otherwise-healthy window; not part of the flagged stall below, which is a different traffic family) |
| Interactive reads (student/attendance/assessment) | 166.6 | 99.8% | 31ms | 203ms | 1,265ms |

This is the honest sustainable mixed capacity at the tested ramp level — no throttle storm, no unexplained backlog growth, latencies bounded.

**Degraded-mode capacity/recovery, per scenario** (injection window + 30s recovery buffer; success% / worst timeout% / p95 / p99, financial-critical path and reconciliation-poll shown as the two highest-volume families — full per-family breakdown for all three scenarios is in the underlying metrics, not reproduced in full here):

| Scenario | financial_critical success | reconciliation_poll success | Notable |
|---|---|---|---|
| `redis-outage` (120s incl. buffer) | 73.0% (16.8% timeout, 10.1% 429) | 89.4% (10.6% timeout) | **`c2b_confirmation` alone hit 89.6% error** in this window specifically — far worse than the blended family figure. `FailOpenRedisCache` correctly never returned a 5xx (0% across every family, every scenario) — the cost of the outage is *latency* (the connection attempt itself blocks for some time before the fail-open catch fires), not functional failure. |
| `kill-worker` (120s incl. buffer) | 100.0% | 99.8% | No measurable HTTP-layer impact at all. Expected: killing the Celery worker doesn't touch the synchronous request/response cycle — only asynchronous background processing (backlog catch-up) is affected, which shows up as backlog growth in the sampler data, not as request failures. |
| `postgres-connection-kill` (45s incl. buffer) | 84.5% (9.7% timeout, 5.7% 429) | 99.1% | See the flagged anomaly below — most of this window's degradation is one clustered event, not spread evenly across the window. |

**Post-recovery capacity** (255s tail after the full chaos sequence has cleared, before drain): financial-critical 99.1%, interactive reads 96.8%, STK push 100%, but **reconciliation polling (`callback_list`) at only 88.8% success (11.2% 429)** — the one family that didn't fully return to its normal-window baseline (99.8%) within this tail window. Plausible explanation: a batch of financial-critical requests stuck for 100+ seconds during the postgres-connection-kill window (see below) only completed after the window's nominal end, so their retry/backoff activity bled into the "post-recovery" bucket by real timestamp even though they were triggered by the chaos injection — a bucketing nuance worth being explicit about rather than reporting the 11.2% as an unexplained lingering regression.

**Flagged finding — a clustered ~110s stall, corroborated two independent ways:** 39 requests (1 `c2b_confirmation`, 4 `callback_list`, 16 `callback_process`, 18 `callback_verify`) across many different tenants all **started** within a 1.3s window (t≈1097.8-1099.1s) and all **completed** within a 0.1s window (t≈1209.3-1209.4s, several with `status_code=200` — no data was lost) — a ~110-111s stall affecting every one of them near-identically, resolving in unison right around when the `postgres-connection-kill` scenario forcibly terminated 8 active PostgreSQL backend connections at +1200s. Independently, the server-side sampler — which polls the database directly, not via HTTP — has a matching ~115s gap in its own otherwise-steady 5s-interval samples (last sample at t=1100.2s, next at t=1215.4s), ruling out an HTTP-client-side (harness) explanation: something genuinely blocked direct database access for this whole window, not just requests routed through gunicorn.

Most likely mechanism, from reading the code (not fully proven — see caveat): `verify_mpesa_callback`/`process_mpesa_callback` (`apps/finance/mpesa_services.py`) take a row-level `select_for_update()` lock on the specific `MpesaCallbackLog` row being verified/processed. This is the *first* run where 5 operators for the same tenant run at genuinely independent concurrency (defect #4's fix) rather than being accidentally serialized by a shared account's throttle — so multiple operators listing the same page of `RECEIVED` callbacks and then racing to verify/process the same rows is now possible for the first time. Zero deadlocks/conflicts were recorded by the sampler in the surrounding samples, and `total_connections` stayed flat at 14 throughout (no connection-pool exhaustion) — consistent with ordinary lock-queueing rather than a deadlock, but *what* held a lock for ~110 seconds before something (plausibly the chaos scenario's forced connection termination) released it is not conclusively identified here. **Not fixed this area** — flagged as a specific follow-up investigation item, not waved off as a harness artifact, since the sampler's own independent stall is real, direct evidence of the database layer, not the test client.

**Follow-up, resolved:** Re-read every `select_for_update()` call site in the implicated path (`apps/finance/mpesa_services.py`) plus `config/settings.py`'s `DATABASES` configuration. Found a real, adjacent defect regardless of what specifically triggered that day's stall: Postgres's `lock_timeout` GUC defaults to 0 (wait forever), and nothing in this codebase ever set one for real runtime connections — telling evidence that this was already a known risk: `apps/finance/test_mpesa_concurrency.py`'s own concurrency tests already work around it locally (`SET lock_timeout = '5s'` per test connection) precisely so an unbounded wait can't hang the test suite itself, but that safety net was never applied outside tests. So whatever held the lock for ~110s that day (the exact trigger remains unidentified, per the honest caveat above) had no bound on how long it could block every request queued behind it — only the later, unrelated `postgres-connection-kill` chaos injection happened to clear it. **Fixed:** `config/settings.py`'s `DATABASES['default']['OPTIONS']` now sets `lock_timeout=5000` (5s, matching the bound the concurrency tests already imposed locally) on every connection; `apps/finance/mpesa_services._select_for_update_or_conflict` wraps the three `MpesaCallbackLog` `select_for_update()` sites (`verify_mpesa_callback`, `process_mpesa_callback`, `reject_mpesa_callback`) to turn a lock-timeout expiry into a clean, retryable `ValidationError` instead of an opaque 500. Verified live against the real Postgres container (a genuinely separate waiting connection, confirmed to receive `django.db.OperationalError` with `error.__cause__` a `psycopg.errors.LockNotAvailable`, `sqlstate == 55P03`), and with a new regression test (`test_stuck_lock_holder_on_a_callback_fails_fast_instead_of_hanging`) that holds a lock for 8s and asserts the waiter fails in well under 7s with the translated message, not a hang. Full finance (167 tests), platform+tenancy (144 tests), and mpesa concurrency (6 tests) suites re-verified green after the change. This does not claim to have identified what specifically held the lock for 110s in `fixed4` — that stays unknown — but it closes the actual defect the finding exposed: an unbounded wait with no self-recovery path. Any future stuck-lock scenario, whatever its cause, now fails fast and visibly instead of silently pinning a worker thread indefinitely.

**Re-verification run: `area6-5e3-fixed5`.** `fixed4` produced the flagged stall, so per this area's own evidence standard, that run shouldn't stand as the final capacity evidence once a real defect was found and fixed in the same code path it measured. Re-ran the identical scenario (20 tenants, `--ramp 100 --operators 5 --stk-rate 5 --interactive-rate 200`, 1500s steady-state, same three-scenario chaos plan, real gunicorn 4×2) with the `lock_timeout` fix in place.

*Environmental hiccup, unrelated to the fix under test:* the first attempt crashed (exit 1) after the host machine went to sleep mid-run — real wall-clock evidence: ~98 minutes elapsed between launch and crash where the chaos plan's own `time.sleep(15)` for `postgres-connection-kill` didn't return until +5809s. On wake, Docker Desktop's host↔container port-forwarding for the backend/fake-daraja ports was left stuck (containers themselves stayed healthy — confirmed by reaching gunicorn directly from inside its own container — only the host-side port mapping was broken; container restart/recreation didn't fix it, only a full Docker Desktop restart did). Retried with `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` held for the run's duration to prevent a repeat. **Not an application defect** — a host/Docker-Desktop-after-sleep issue, worth remembering for any future long unattended run on this machine.

*Known contamination in the raw reconciliation count:* both the crashed attempt and its clean retry used the same `--run-id area6-5e3-fixed5`, so some of the crashed attempt's own partial traffic (whatever reached the backend before the port-forwarding broke, real callback log rows sharing the same `LT-area6-5e3-fixed5-*` trans_id prefix) is mixed into the retry's data — confirmed directly: 4,111 total `MpesaCallbackLog` rows carry that prefix against the clean retry's own harness-reported 2,514 events. The raw reconciliation violation count this produces (**534, all `missing_payment`, zero `duplicate_payment`, zero `cross_tenant_contamination`** — verified directly against the database: 694 rows `RECEIVED`, all `attempts=0` with empty `last_error`, durably queued not lost) is therefore **not directly comparable** to `fixed4`'s 479 and isn't cited as a clean capacity figure. The hard pass/fail gate (zero duplicate/cross-tenant) still holds regardless of the contamination.

*What is clean and trustworthy:* the harness's own latency/throughput/error-rate metrics (measured from the successful retry's own HTTP responses in-process, never touching the contaminated DB row count) and the chaos timing. Both directly speak to the fix under test:
- `postgres-connection-kill` **recovered at +1216s against a scripted +1200s injection / 15s duration** — essentially on schedule, no repeat of the ~110s anomalous stall.
- The sampler's own direct-DB `lock_waits` metric (`count(*) FROM pg_locks WHERE NOT granted`, sampled every 5s) registered non-zero in only 4 of ~340 samples across the whole run, every one an isolated singleton (`= 1`, never two consecutive samples) — a clean signature, in sharp contrast to what a sustained multi-minute lock-block would show. Zero deadlocks throughout.
- Sustained mixed capacity this run: `c2b_confirmation` 2,514 events/1500s (14.08% error rate, driven by the chaos windows — p50 187ms, p99 15,156ms reflecting the chaos-window outliers, not a sustained failure), `callback_list` 41,273 requests (2.12% error), `callback_verify`/`callback_process` ~3,008 each (<1% error, p50 ~300ms) — broadly consistent with `fixed4`'s own normal-window figures, no regression from the fix.

**Classification:** the lock_timeout fix's intended effect — no more unbounded, self-recovering-only-by-luck lock waits — is directly corroborated: this run shows no sustained `lock_waits` signature and no recurrence of the stall shape under the same chaos scenario that originally exposed it. This is not proof the *original* fixed4 trigger is identified (it still isn't — see above), only that the failure mode it exposed no longer reproduces under the same conditions. Full report: `backend/loadtest/reports/area6-5e3-fixed5.md`.

**Noisy-tenant weighting, verified at full 5E-3 scale for both traffic shapes:** C2B ramp (loadtest-0: 799 events vs. 81 each for the other 19) — **9.86:1** against a 10:1 target, matching 5E-1's precedent. Interactive reads (loadtest-0: 1,023 vs. ~157 each) — only **6.50:1**. Root cause, not a weighting-logic bug (the rate multiplier itself is applied correctly, confirmed by reading `harness.py`): the noisy tenant's intended interactive-read rate (`200 × 10/29 ≈ 69/min ≈ 4,140/hour`) exceeds even a single dedicated registrar account's `1000/hour` throttle budget by itself, so the noisy tenant's own interactive-read traffic self-throttles (the same 20s-on-429 backoff from defect #3) and under-delivers against its weighted target, while the 19 normal tenants (~7/min ≈ 420/hour each, safely under budget) achieve close to full rate. A further real-world instance of the same accepted per-user-not-per-tenant throttle property already documented at 5E-1/5E-2/defect #4 above — not a new class of issue.

**Integrity:** `loadtest_reconcile --run-id area6-5e3-fixed4` reported **479 violations, all `missing_payment`, zero `duplicate_payment`, zero `cross_tenant_contamination`.** Verified directly against the database (not inferred from the reconcile output alone, per this area's standing practice): 362 `MpesaCallbackLog` rows remained `RECEIVED` at drain-timeout, every one with `attempts=0` and empty `last_error` — durably queued, awaiting processing, not lost, corrupted, or duplicated. The harness's own drain-phase log is explicit about this: *"drain timeout reached -- some callbacks may still be RECEIVED (a real capacity finding, not a bug in the check)"* — Celery's processing throughput lagging the production rate under peak/chaos load by the time the bounded drain window closed is itself a legitimate capacity data point, not an integrity failure. Down from 1,639 violations in the prior (defect #4-contaminated) `fixed3` run, despite `fixed4` carrying 2.7x more total traffic — direct evidence the fix reduced real backlog pressure, not just reduced apparent error counts.

**Classification:**
- Defects #1-4 above: **harness defects, fixed**, each with live verification before being trusted.
- Redis-outage's elevated timeout rate (esp. `c2b_confirmation`'s 89.6%): **capacity-planning observation, not a code defect** — `FailOpenRedisCache` behaved correctly (zero 5xx), the cost is connection-attempt latency during an outage, which is inherent to any cache-backed throttle without a very aggressive connect timeout on the cache client itself. No fix applied this area; worth a future look at tightening the Redis client's own connect timeout if a 30s+ degraded window during a Redis outage is judged unacceptable for production.
- Kill-worker: **clean result** — no finding, no action needed.
- The ~110s clustered stall during postgres-connection-kill: **root trigger not conclusively identified, but the defect it exposed is fixed** — see the follow-up paragraph above. `lock_timeout=5000` now bounds every `select_for_update()` wait in the codebase, converting an indefinite hang into a fast, clean, retryable error regardless of cause.
- Noisy-tenant interactive-read weighting shortfall: **capacity-planning observation**, same accepted per-user-throttle property already on record, not a new defect.

**Honest capacity statement:** sustainable mixed end-to-end capacity at the tested ramp (100 events/min C2B, 5 operators, 5/min STK, 200/min interactive across 20 tenants) is **~455 req/min financial-critical, ~2,137 req/min reconciliation polling, ~87 req/min STK push, ~167 req/min interactive reads, all ≥99.2% success**, with bounded, healthy latency. Under the tested chaos sequence, `redis-outage` and `postgres-connection-kill` produce real, measured, bounded-duration degradation (no data loss in any case); `kill-worker` produces none at the HTTP layer. The system returns to within-normal-range capacity across every traffic family within the 255s post-recovery tail, with one caveat (reconciliation polling) explained above as a bucketing artifact of chaos-triggered retries, not genuine unrecovered degradation.

Full per-phase/traffic-class latency table, reconciliation output, and per-5s server-side samples: `backend/loadtest/reports/area6-5e3-fixed4.md`, `-samples.json` (generated, not reproduced in full here).

### Soak run — `area6-soak1` (sustained 8h, no chaos) ✅ MEASURED

`fixed4`/`fixed5` each ran a 1500s (25min) steady-state window — long enough to prove the lock_timeout fix and measure chaos-recovery, but structurally too short to tell "processing is lagging but keeps up" apart from "processing is falling behind and never catches up," since either looks the same in a 25-minute snapshot. This run answers that specifically: same `fixed5` traffic mix (20 tenants, 5 operators/tenant, 5 STK/min, 200 interactive-reads/min, real gunicorn 4×2, shared Redis throttle cache) held **flat for 8 hours with no chaos injection** (`--ramp 100 --step-duration 28800 --drain-timeout 120`), sampler polling the database directly every 5s for the same window. Ran as a genuine unattended overnight background task (2026-09-12 06:33-14:35, real wall-clock, `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)` held for the full duration this time — no repeat of `fixed5`'s host-sleep/Docker-Desktop port-forwarding incident, see [[docker_desktop_cli_path]]).

**Latency — flat across all 8 hours, no leak/growth signature.** Per-hour p50/p99 for every traffic class stayed within noise of its own hour-0 value for the entire run — e.g. `c2b_confirmation` p50 63-94ms / p99 141-160ms in every one of 8 hourly buckets, `callback_list` p50 pinned at 16ms / p99 at 47ms from hour 1 onward. No traffic class shows a rising trend at any point, which is the core thing a multi-hour soak is for: a slow resource leak (connection exhaustion, unbounded queue growth inside a worker, GC pressure) would show up as latency creeping upward hour over hour, and none does.

**Error rate — high but entirely throttle-shaped, not failures.** `callback_verify`/`callback_process`/`callback_list` show high raw error rates (43-78%, decreasing hour over hour as operators' own 20s-on-429 backoff self-adjusts their effective request rate down) but the status-code breakdown is unambiguous: `callback_verify` 15,909×200 / 23,059×429 (zero other codes), `callback_process` 14,841×200 / 23,712×429 / 415×400, `callback_list` 81,652×200 / 14,793×429 / 114×null (connection-level, not server errors). Zero 5xx anywhere. The 429s are the same already-documented per-user-not-per-tenant `user: 1000/hour` throttle ceiling flagged repeatedly at 5E-1/5E-2/5E-3's noisy-tenant weighting — just far more visible here because operators sustain effort for 8 continuous hours instead of a 25-minute burst, so they exhaust their hourly budget and stay throttled instead of finishing before hitting it. Not a new defect. The 415 `callback_process` 400s are the `_select_for_update_or_conflict` `ValidationError` translation from the `72c32b1` fix firing under real sustained lock contention — expected, healthy behavior (a fast retryable error, not a hang), not an anomaly.

**Backlog growth — linear and bounded, not accelerating.** `MpesaCallbackLog` rows for this run: 46,153 total, 32,841 `RECEIVED` / 13,312 `PROCESSED` at end-of-run. The sampler's own `callback_backlog_count` grew by a remarkably constant ~4,030-4,080 rows every single hour for all 8 hours (694 → 5,062 → 9,123 → 13,154 → 17,207 → 21,257 → 25,310 → 29,358 → 33,384) — dead-linear, not accelerating (which would indicate a worsening leak) and not plateauing (which would indicate eventual catch-up). Confirmed independently via direct DB query, not just the sampler: rows created in hour 1 (had ~7 more hours to be picked up) show the same ~70% still-`RECEIVED` rate as rows created in hour 7 (right at the end) — old backlog isn't being preferentially drained over time, so this is a durable, predictable capacity ceiling at the tested operator concurrency (5/tenant against 100 events/min inflow), not an open-ended growth problem. All `RECEIVED` rows verified `attempts=0` with empty `last_error` — durably queued, not lost, corrupted, or duplicated.

**Database layer — clean over the full 8h, same signature as `fixed5`.** Sampler: 5,855 samples. `total_connections` bounded the whole run (9-16, settling at 14 — no connection leak). `lock_waits` nonzero in only 8 of 5,855 samples, every one an isolated singleton (longest consecutive-nonzero streak: 1) — the same clean signature `fixed5` showed at 25 minutes, now proven to hold at 8-hour sustained scale rather than just a short burst. Zero deadlocks, zero conflicts, for the entire run.

**Integrity:** `loadtest_reconcile --run-id area6-soak1` reported **34,630 violations, all `missing_payment`, zero `duplicate_payment`, zero `cross_tenant_contamination`** — the hard pass/fail gate holds. The much larger raw count than `fixed4`'s 479 is expected and not comparable (this run's C2B ramp alone ran ~19x longer), not a regression.

**Classification:** no new defect. This run's purpose was specifically to rule out slow degradation that a 25-minute window can't see, and it does: latency and connection count are flat for 8 continuous hours, the `lock_timeout` fix's clean `lock_waits` signature holds at sustained scale (not just `fixed5`'s short burst), and the backlog growth that does occur is linear/bounded and already explained by the same per-user throttle property on record since 5E-1 — not a new or worsening issue. Full report: `backend/loadtest/reports/area6-soak1.md`, `-samples.json`, `-metrics.json` (generated, not reproduced in full here).

### Area 6 summary

Reviewing the full retained evidence set together (5E-1/5E-1-explore, the shared-throttle-cache defect, 5E-2, 5E-3/`fixed4`, 5E-3/`fixed5`, and the `area6-soak1` soak run) before closing this area, per this project's RC process:

- **Four real defects found and fixed**, each in the load-test tooling itself or the application code it exercised, each with its own commit and live re-verification before validation continued: the tenant-weighting bug (`062475e`), the missing-auth-header bug (`1d2e028`), the per-worker throttle-cache bug (`9861d41`), and the unbounded `select_for_update()` lock wait (`72c32b1`). None were waved off as harness artifacts without proof; each was traced to a specific line of code and confirmed fixed against the real stack.
- **The ~110s stall that drove the `72c32b1` fix**: its exact original trigger was never conclusively identified, and that stays true — this summary does not upgrade that claim. What's proven is narrower and real: an unbounded Postgres lock wait existed with no self-recovery path, closing that gap converts any future stuck-lock scenario (whatever causes it) into a fast, clean, retryable error instead of an indefinite hang, and two independent re-verification runs at different time scales (`fixed5` at 25 minutes under the same chaos sequence, `area6-soak1` at 8 hours with continuous sustained load) both show the fix's intended clean signature — no sustained `lock_waits`, no recurrence of the stall shape.
- **Verified capacity, at the tested configuration**: ~44/s sustained financial-processing capacity with zero DB contention (5E-2); ~455 req/min financial-critical, ~2,137 req/min reconciliation polling, ~87 req/min STK push, ~167 req/min interactive reads, all ≥99.2% success under normal conditions (5E-3); no leak or degradation signature (flat latency, bounded connection count, zero deadlocks) across 8 continuous hours of sustained load (soak).
- **One recurring, already-accepted capacity-planning property, now measured at its real magnitude**: the per-user-not-per-tenant `user: 1000/hour` DRF throttle ceiling was first flagged as a minor weighting artifact at 5E-1/5E-2/5E-3 scale (a few percentage points of under-delivery for one noisy tenant). At 8-hour sustained scale it's the dominant effect on `callback_verify`/`callback_process`/`callback_list` (43-78% of requests throttled, all clean 429s, zero 5xx) — not a defect, but **worth an explicit go-live decision**: if real back-office M-Pesa reconciliation staff are expected to sustain verify/process volume near or above 1000 requests/hour per account during a busy period, this ceiling will visibly throttle them the same way it throttled the load-test operators here. No code change made this area since the throttle is doing exactly what it was configured to do; flagging it for a product/ops decision (raise the authenticated-user rate, or scope M-Pesa operator accounts to their own higher throttle scope) rather than silently carrying it forward.
- **Chaos-recovery findings** (`redis-outage`'s latency-only degradation with zero 5xx, `kill-worker`'s clean no-impact result) stand as measured in 5E-3 and were not re-tested in the soak run by design — the soak run deliberately excluded chaos injection to isolate for gradual degradation specifically, which chaos-recovery testing already covered.
- **Integrity, across every run in this area**: zero `duplicate_payment`, zero `cross_tenant_contamination` — the hard pass/fail gate held in every single measurement, including the highest-volume run (`area6-soak1`, 46,153 callback rows, 34,630 raw `missing_payment` violations, all durably queued with `attempts=0`/empty `last_error`, none lost or corrupted).

**Closing this area** on that basis: every defect the evidence surfaced has its own fix, commit, and live re-verification; the capacity figures above are real measurements against the live stack, not projections; and the one unresolved item (the per-user throttle ceiling at sustained scale) is explicitly carried forward as a go-live decision rather than closed out quietly.

## Area 7 — Operational resilience
*In progress.*

**Baseline under test:** `769fd69` (Area 6 close). **Date started:** 2026-09-12.

**Scope:** Areas 5-6 already proved resilience under *component-level* failure while the rest of the stack kept running (Redis outage, worker `SIGKILL`, redelivery races, one Postgres connection killed mid-transaction, bounded lock waits, 8h sustained load with zero leak). Area 7 does not repeat any of that. It closes the remaining gaps specific to *operational* events — restart, redeploy, cold start, and whole-stack interruption — that a production deployment will actually encounter but that no prior area exercised: a full Postgres/Redis container restart (not one connection), a graceful (not crashed) Celery worker/Beat restart, a full-stack `down`/`up` cycle with durable work genuinely in flight, cold startup ordering, and a live backend redeploy.

**Ground rule:** run the existing system as-is. Do not preemptively add Postgres/Redis healthchecks, wait-for scripts, Beat scheduler configuration, or restart policies before any check demonstrates a concrete failure. If a check does demonstrate one, classify it and make the smallest justified correction — this area is measurement first, same discipline as Areas 5-6.

**Overarching invariant, every check:** infrastructure interruption → requests may temporarily fail cleanly → service recovers automatically → **NO** cross-tenant contamination, duplicate financial effect, partial committed transaction, permanently stuck durable work, runaway retry/backlog, or manual database repair. A 503 during a real Postgres restart can be entirely correct behavior; corrupting or duplicating a payment while avoiding that 503 is not — availability and correctness are judged separately for every check.

**Backup/restore — operational-readiness finding, recorded at scoping (no test needed to discover it):** no backup/restore tooling, script, or documented recovery procedure exists anywhere in this repository for PostgreSQL data (finance, student, attendance, assessment, audit, document records) as of this baseline — confirmed by searching the full repo tree. **Not fixed in Area 7 itself** (building backup infrastructure now would be inventing a new subsystem, not proving resilience of what exists), but explicitly **not accepted as an ordinary risk either** — a school ERP holding finance/student/audit data must not reach go-live without a defined recovery mechanism. Required before go-live: decide whether the deployment target supplies managed PostgreSQL backups (and document the RPO/RTO and restore procedure it provides) or build a dedicated backup job, then run an actual restore drill as its own bounded gate once a mechanism is chosen. Carried to Area 8 for explicit sign-off, same treatment as Area 3's go-live acceptance decisions.

**Checklist, in approved execution order** (cold-start first since a broken startup ordering would compromise every later full-stack check; backend redeploy last since it's partly a deployment-architecture question rather than application correctness):

| # | Check | Status |
|---|---|---|
| 1 | Cold startup / dependency readiness | ✅ PASS |
| 2 | PostgreSQL full container restart under live load | ✅ PASS |
| 3 | Redis full container restart, cache/throttle path | ✅ PASS |
| 4 | Celery worker graceful restart mid-task | Paused mid-check (see note) |
| 5 | Celery Beat restart — schedule integrity | Not started |
| 6 | Full-stack outage with durable work in-flight | Not started |
| 7 | Backend redeploy under live traffic | Not started |

### Check 1 — Cold startup / dependency readiness ✅ DONE

**Claim under test:** `docker-compose.yml` has no `healthcheck`/`condition: service_healthy` on `postgres` or `redis` — plain `depends_on` only sequences container *start*, not readiness. Does `backend`/`celery-worker`/`celery-beat` tolerate Postgres/Redis not yet accepting connections at the moment they start, or does a fresh `docker compose up` crash-loop?

**Method:** `docker compose down` (no `-v` — confirmed the named volume `schoolmanagementsystem_postgres_data` survived) removing every container and the network, then `docker compose up -d` from fully stopped, with per-second `/readyz/` polling and full container logs captured from the moment of `up`.

**Observed:** zero crash loops — `RestartCount=0` on every container (`postgres`, `redis`, `backend`, `celery-worker`, `celery-beat`) after settling. No connection-refused or retry errors in any log. Timeline from `up -d` (t=0): `postgres`/`redis` containers report `Started` almost immediately; `backend`'s dev server (autoreload + system checks) doesn't start listening until **t≈16s**, first successful `/readyz/` 200 at **t≈19s**; `celery-worker` connects to Redis and reports `ready` at **t≈15s**, immediately picks up 3 already-due Beat tasks; `celery-beat` starts dispatching scheduled tasks at **t≈12s**. No task or request failed.

**Honest caveat — this run did not actually force the race it set out to probe.** Postgres/Redis (`-alpine` images, pre-initialized volume, no fresh `initdb`) become ready in a couple of seconds, well inside the ~12-19s head start that Django's own multi-stage dev-server boot (autoreloader, system checks) and Celery's own startup sequence naturally provide even with no explicit wait mechanism. So "no crash observed" is real evidence of this specific run, but doesn't independently prove the case where a request/connection attempt genuinely arrives before Postgres/Redis are listening.

**That losing case is nonetheless covered, by code inspection rather than by this run:**
- Celery: `CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True` (`config/settings.py`) is explicit, not a default left to chance — worker/beat retry-connect to Redis on boot instead of crashing if it isn't ready yet.
- Django/gunicorn: DB access is lazy and per-request, not a boot-time dependency — gunicorn's master/worker processes start accepting connections immediately regardless of Postgres state. A request arriving before Postgres is reachable gets a clean, already-proven-clean failure (`/readyz/`'s hard-Postgres-dependency 503, Area 1 bonus check) on that one request, not a process crash — the next request retries a fresh connection.
- A genuinely fresh volume (first-ever boot, real `initdb`) would give Postgres *more* absolute startup time, not less, and that time entirely precedes Postgres opening its listening socket — `depends_on`'s lack of a health condition doesn't change this dynamic, it only affects the (harmless, self-healing) window of individual failed requests before Postgres is reachable.

**Integrity invariant:** N/A for this check (availability/operability only, no data path exercised).

**Classification:** **PASS**, no defect. The stated ground rule (don't preemptively add healthchecks) holds — the two mechanisms that make this safe already exist for the reasons that matter (explicit Celery startup retry, Django's inherently lazy/self-healing DB access), not by accident. No code change made.

### Check 2 — PostgreSQL full container restart under live load ✅ DONE

**Claim under test:** Area 5 Check 4 proved a *single* connection killed mid-transaction rolls back cleanly. A full `docker compose restart postgres` is a different failure mode — every connection is severed at once, not one at a time, and there's a genuine (if brief) window where Postgres isn't listening at all. Does the same clean-rollback/no-corruption guarantee hold under a real whole-instance restart with many concurrent transactions in flight, not just one isolated connection?

**First attempt invalidated, recorded rather than discarded silently:** ran the harness's 5e3 traffic mix against the **plain dev stack** (its default `5/min` login throttle, no `THROTTLE_RATE_LOGIN` override). All 60 background traffic tasks (operator pools/STK trickle/interactive reads across 20 tenants) hit `429` on their very first login call almost immediately — well before the restart — and the harness crashed with an unhandled `HTTPStatusError` before ever calling `recorder.write()`, so no metrics were saved. Own mistake: this tooling assumes the loadtest overlay's raised `THROTTLE_RATE_LOGIN=1000/min` (`docker-compose.loadtest.yml`), which the plain stack doesn't have. Corrected by bringing up the loadtest overlay (matching Area 6's own established methodology) and re-running.

**Method (valid run):** loadtest overlay (real gunicorn 4×2, shared Redis cache), harness `--phase 5e3 --run-id area7-check2-pg-restart --ramp 100 --step-duration 300 --operators 3 --stk-rate 5 --interactive-rate 100` against the 20 existing loadtest tenants. `docker compose restart postgres` issued ~93s into the run while traffic was flowing, with `/readyz/` polled every 1s throughout.

**Observed:**
- `/readyz/` stayed 200 on every sampled second — the actual outage window was short enough to fall between 1s samples (an earlier, separate poll during the invalid first attempt did catch one 503 at t+1s, back to 200 by t+2s, consistent with a sub-2-second real gap for a graceful `postgres` container restart, not a crash-and-recover).
- Harness-level errors: 22 of 2,098 events in the ±10s/+30s window around the restart, **all clustered in a ~2.7s window** (t+0.43s to t+3.17s relative to the restart) — `callback_verify`/`callback_process`/`callback_list` 500s, one `stk_push` 500, one `c2b_confirmation` 500, and **3 `callback_process` 400s carrying the `_select_for_update_or_conflict` `ValidationError` translation from the Area 6 `lock_timeout` fix (`72c32b1`)** — the fix correctly converting real lock contention during the recovery scramble into a clean, retryable error rather than a hang or an opaque 500. No errors of any kind from t+3.17s onward in the sampled window (two much-later, unrelated singleton events at t+17.78s/t+23.76s — an ordinary `404` and a connection hiccup, isolated, not part of the restart cluster).
- Integrity: `loadtest_reconcile --run-id area7-check2-pg-restart` — **335 violations, all `missing_payment`, zero `duplicate_payment`, zero `cross_tenant_contamination`** (the hard gate holds). Verified directly against `MpesaCallbackLog`, not just the reconcile summary: 989 rows total, every `PROCESSED` row has both `processed_at` and `verified_at` set (zero missing either), every `RECEIVED` row has `attempts=0` and empty `last_error` (durably queued, not corrupted or silently failed), zero `REJECTED`. No row anywhere shows a partial or inconsistent state from the restart.

**Integrity invariant:** held — no partial commit, no duplicate financial effect, no cross-tenant contamination, service recovered automatically with zero manual intervention.

**Classification:** **PASS**, no defect. A full Postgres restart under live load behaves exactly like the invariant predicts: a short, real, correctly-surfaced availability gap (clean 5xx/400s clustered tightly around the restart, not spread out or hidden), zero correctness violations, automatic recovery. No code change made — this is the same guarantee Area 5 Check 4 proved for one connection, now shown to hold under a real whole-instance restart with concurrent traffic too.

### Check 3 — Redis full container restart, cache/throttle path ✅ DONE

**Claim under test:** Area 5 proved the Celery-broker-reconnect leg of a Redis outage (`docker compose stop`/`start redis`, worker reconnects on its own). This checks the leg Area 5 didn't: `FailOpenRedisCache` (the throttle/cache client), under a live `docker compose restart redis` (not stop/start) while real mixed traffic — including throttled endpoints — is flowing. Same physical Redis instance backs both the Celery broker (`db0`) and Django's cache (`db1`) in this compose setup, so this check necessarily exercises both paths at once, not cache in isolation — noted as environmental fact, not a test flaw.

**Method:** loadtest overlay, harness `--phase 5e3 --run-id area7-check3-redis-restart` (same parameters as Check 2), `docker compose restart redis` issued ~90s into the run, `/readyz/` polled every 1s with its full JSON body captured (to see the `cache: degraded` state specifically, not just the status code).

**Observed:**
- `/readyz/` stayed `{"database": "ok", "cache": "ok", "status": "ok"}` on every sampled second — the actual outage window was too brief to land on a 1s sample (Area 1 already directly proved the `degraded` state fires correctly under a sustained `docker compose stop redis`; this restart's real gap was evidently shorter than that).
- Harness-level errors in the ±10s/+40s window (2,445 events): **zero 5xx of any kind** — only 3 ordinary `c2b_confirmation` `404`s (scattered, not clustered at the restart, unrelated) and 26 `callback_list` `429`s (ordinary throttle enforcement). This is the `FailOpenRedisCache` design working exactly as intended, live, under real concurrent traffic, not just reasoned from code.
- Throttle behavior across the restart is itself informative: 429s occurred steadily before the restart (t-8 to t-1.3s) and resumed steadily from t+8.96s onward, with **no 429s in between** (t-1.3s to t+8.96s, a ~10s window) — consistent with the cache briefly failing open (requests let through, unthrottled but not erroring) during the actual outage, then throttling correctly re-enforcing once the cache reconnects. Exactly the intended fail-open-then-recover behavior, not silently broken.
- Celery broker leg, confirmed via `celery-worker` logs: `21:37:26.953 WARNING consumer: Connection to broker lost. Trying to re-establish the connection...` → `21:37:28.998 INFO Connected to redis://redis:6379/0` → mingle → resumed receiving tasks by `21:37:52`. ~2s automatic reconnect, no manual intervention — generalizes Area 5's stop/start-based proof to a live `restart` under real load too.
- Integrity: `loadtest_reconcile --run-id area7-check3-redis-restart` — **144 violations, all `missing_payment`, zero `duplicate_payment`, zero `cross_tenant_contamination`.**

**Integrity invariant:** held — zero 5xx caused by the outage itself, zero correctness violations, automatic recovery on both the cache and broker legs with no manual intervention.

**Classification:** **PASS**, no defect. Confirms Area 1's fail-open design and Area 5's broker-reconnect proof both hold under a live container restart with real concurrent traffic, not just the more controlled conditions each was originally proven under. No code change made.

### Opportunistic finding — backend container replacement / nginx upstream DNS lifecycle ✅ FIXED

Not one of the original seven checks — discovered incidentally while working Check 4 (a `backend` container recreation to apply that check's `exec` fix), then reconfirmed live when the user's own browser hit it for real. Recorded here rather than folded silently into Check 4, per this area's own evidence standard.

**Finding:** `frontend/nginx.conf` proxied `/api/` to `http://backend:8000` with a bare hostname and no `resolver` directive — nginx resolves that hostname's IP once and caches it for the `frontend` container's entire lifetime (no periodic re-resolution). Any time `backend` is recreated (a redeploy, or this session's own `docker compose up -d backend`) while `frontend` keeps running, Docker assigns the new container a different internal IP, but nginx keeps sending every proxied request to the old, now-dead address — **every API call 502s until `frontend` itself is restarted**, even though both services are otherwise completely healthy. Confirmed directly, not inferred: `frontend` started at `18:17:42`, `backend` was recreated at `18:46:40` with a new IP (`172.21.0.5`, was `172.21.0.4`), and nginx's own error log showed `connect() failed (111: Connection refused)` against the stale address while a parallel `docker exec frontend curl http://backend:8000/...` (a fresh DNS lookup, not nginx's cached one) succeeded immediately — isolating the bug to nginx's resolution caching specifically, not general connectivity, DNS, or backend health.

**Fixed:** `frontend/nginx.conf` now sets `resolver 127.0.0.11 valid=10s;` (Docker's embedded DNS, 10s TTL) and moves `proxy_pass` to a `set`-assigned variable (`$backend_upstream`) rather than a literal hostname — the standard, well-established nginx pattern for this exact class of bug, since a literal-hostname `proxy_pass` is resolved once at config load while a variable-based one re-resolves per the `resolver` TTL.

**Verified live:** rebuilt `frontend`, then recreated `backend` **twice** in a row while `frontend` was left completely untouched. Both times, API calls through nginx recovered cleanly with no persistent 502s and no frontend restart needed (confirmed via `loadtest`-independent direct calls to `/api/v1/session/`, 200 within ~150ms of backend finishing its own boot). Before the fix, a single recreation was sufficient to break it, as directly demonstrated moments earlier in this same investigation.

**Classification:** **RC defect — fixed and verified**, commit (nginx.conf only, code change). **Integrity impact:** availability/routing only — no evidence of financial or tenant-data corruption; this is a request-routing failure, not a data-path one. Directly relevant to Check 7 (backend redeploy under live traffic, not yet run) — this failure mode is now closed ahead of that check, rather than left to make Check 7 fail for an unrelated reason.

### Check 4 — Celery worker graceful restart mid-task ⏸️ PAUSED mid-check

**Real defect found and fixed before the main experiment could even run:** `docker top` on `celery-worker` showed PID 1 was `sh -c "pip install -r requirements/loadtest.txt && celery -A config worker ..."` — the shell, not celery — because `docker-compose.loadtest.yml`'s `command:` chains via `&&` without `exec`. This is the exact same signal-forwarding defect the Dockerfile's own gunicorn `CMD` already had to fix in Milestone 22.3 (see its comment), reintroduced here. It also affected `backend`'s gunicorn command in the same overlay file. **Fixed**: added `exec` before the final command in both `docker-compose.loadtest.yml` overrides (`backend`, `celery-worker`); verified via `docker top` post-fix that PID 1 is now the real process in both containers, not a wrapping shell. This means every graceful-shutdown-under-live-load check already run this area (Checks 2-3) benefits from the fix going forward, though it doesn't retroactively invalidate them (neither tested backend's/celery's own restart).

**Remaining part of this check, not yet completed:** engineering a real, Celery-dispatched task to genuinely block mid-execution (to observe whether the now-correctly-signaled graceful restart lets it finish rather than needing Area 5's stale-lease-reclaim safety net) proved harder than expected — `apps.activity.durable_work.claim_due()` uses `select_for_update(skip_locked=True)` by design (Area 5 Check 3), so it never blocks on a locked row, it skips it; a second attempt using `apps.finance.tasks.resweep_unmatched_incoming_payments` (which does use a plain, blocking `select_for_update()` inside `_attempt_auto_match`) short-circuited before reaching that lock because the synthetic `IncomingPayment` used to trigger it didn't satisfy `_recognize_student_from_reference`'s matching precondition. Paused here rather than continuing to force a contrived business scenario — picked back up per the user's next direction, interrupted by a higher-priority frontend performance report mid-session. `celery-beat` (stopped during setup to remove an unrelated race) was restarted before pausing; no other state left dangling.

## Area 8 — RC evidence and decision
*Not started — this section becomes the final sign-off once Areas 1-7 close.*

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

## Area 5 — Concurrency & failure recovery
*Not started.*

## Area 6 — Performance & capacity
*Not started.*

## Area 7 — Operational resilience
*Not started.*

## Area 8 — RC evidence and decision
*Not started — this section becomes the final sign-off once Areas 1-7 close.*

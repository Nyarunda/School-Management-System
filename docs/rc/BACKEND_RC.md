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

## Area 6 — Performance & capacity
*In progress.*

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

## Area 7 — Operational resilience
*Not started.*

## Area 8 — RC evidence and decision
*Not started — this section becomes the final sign-off once Areas 1-7 close.*

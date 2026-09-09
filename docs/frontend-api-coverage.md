# Frontend/Backend API Coverage

The authoritative, kept-current integration ledger. Updated whenever a wiring slice lands — see `docs/frontend-architecture.md`'s "Coverage-audit backlog" for sequencing. **Connected** means a frontend `api()`/`apiDownload()` call reaches the endpoint. **Workflow complete** means an operator can actually finish the real task the endpoint exists for (not just "a request fires"). A row can be Connected but not yet Workflow complete.

Rows below with exact paths were confirmed by reading the actual backend `urls.py`/`api.py` and the actual frontend call site — not inferred. Domains not yet given a dedicated wiring pass (marked "not yet detailed") are tracked at summary level only, using the original 2026-09-09 audit's counts; their exact endpoint list gets filled in here when that domain's slice is picked up, rather than guessed now.

## Finance (core) — Partial (Credit Note creation and Allocation Reversal closed; Payment detail, Ledger, and Fee Category/Item creation still open)

| Endpoint | Method | Permission | Frontend | Connected | Workflow complete |
|---|---|---|---|---|---|
| `/finance/fee-structures/` | GET/POST | `finance.fee_structure.view`/`.create` | `features/finance.tsx` `FeeStructuresPage` | ✅ | ✅ |
| `/finance/fee-structures/<id>/lines/` | POST | `finance.fee_structure.edit` | `FeeStructuresPage` | ✅ | ✅ |
| `/finance/fee-structures/<id>/approve/` | POST | `finance.fee_structure.approve` | `FeeStructuresPage` | ✅ | ✅ |
| `/finance/student-fee-assignments/` | GET/POST | `finance.fee_structure.view`/`.edit` | `AssignmentsPage` | ✅ | ✅ |
| `/finance/student-fee-assignments/<id>/generate-invoice/` | POST | `finance.invoice.create` | `AssignmentsPage` | ✅ | ✅ |
| `/finance/invoices/` | GET | `finance.invoice.view` | `InvoicesPage` | ✅ | ✅ (raw student id — `STUDENT-GAP-02`) |
| `/finance/invoices/<id>/issue/` | POST | `finance.invoice.issue` | `InvoicesPage` | ✅ | ✅ |
| `/finance/credit-notes/` | POST | `finance.credit_note.create` | `InvoicesPage` — new row action on `ISSUED` invoices, closed in the Invoices List Workspace migration | ✅ | ✅ |
| `/finance/credit-notes/` | GET | `finance.student_account.view` | none | ❌ | Backend has no `invoice`/`student` filter, so a bounded "credit notes for this invoice" view isn't possible without over-fetching the full tenant-wide list; a standalone Credit Notes browsing page wasn't part of this slice's scope |
| `/finance/payments/` | GET/POST | `finance.payment.view`/`.record` | `PaymentsPage` | ✅ | ✅ (raw student id — `STUDENT-GAP-02`) |
| `/finance/payments/<id>/allocate/` | POST | `finance.payment.allocate` | `PaymentsPage` | ✅ | ✅ |
| `/finance/payments/<id>/reverse/` | POST | `finance.payment.reverse` | `PaymentsPage` | ✅ | ✅ |
| `/finance/payments/<id>/` | GET | `finance.payment.view` | none | ❌ | Redundant today — the list row already carries everything this serializer returns |
| `/finance/payment-allocations/<id>/reverse/` | POST | `finance.allocation.reverse` | `PaymentsPage` — per-allocation "Reverse" action inside the payment detail dialog, kept visually and semantically distinct from whole-payment reversal | ✅ | ✅ (no client-side "remaining amount" cap — `PaymentAllocationSerializer` doesn't expose reversed-to-date, so the backend's rejection is the authoritative guard; verified live) |
| `/finance/ledger-entries/` | GET | `finance.student_account.view` | none | ❌ | Not yet detailed |
| `/finance/incoming-payments/` | GET | `finance.reconciliation.view` | `IncomingPage` | ✅ | ✅ |
| `/finance/incoming-payments/<id>/match/` | POST | `finance.reconciliation.match` | `IncomingPage` | ✅ | ✅ |
| `/finance/incoming-payments/<id>/ignore/` | POST | `finance.reconciliation.ignore` | `IncomingPage` | ✅ | ✅ |
| `/finance/payment-methods/` | GET | `finance.payment.record` | `IncomingPage`/`PaymentsPage` lookup | ✅ | ✅ |
| `/finance/setup/` | GET | `finance.setup.view` | `pages.tsx` `SetupPage` (read-only) | ✅ | Partial — no edit UI (out of scope this slice) |
| `/finance/students/<id>/finance/` | GET | `finance.student_account.view` | `StudentPage` Fees tab | ✅ | ✅ |
| `/finance/fee-categories/`, `/finance/fee-items/` | POST | `finance.setup.manage` | none | ❌ | Read-only today (`FeeStructuresPage` only lists existing items for the line-add picker) — no UI to create a new category/item |
| `/academics/academic-years/`, `/academics/academic-levels/` | GET | `academics.setup.view` | `FeeStructuresPage` | ✅ | ✅ |

## M-Pesa — Full

| Endpoint | Method | Permission | Frontend | Connected | Workflow complete |
|---|---|---|---|---|---|
| `/finance/mpesa/stk-push/` | POST | `finance.mpesa.stk_push.initiate` | `mpesa.tsx` `StkWorkspace` | ✅ | ✅ |
| `/finance/mpesa/stk-requests/` | GET | `finance.mpesa.stk_push.view` | `StkWorkspace` | ✅ | ✅ |
| `/finance/mpesa/stk-requests/<id>/query/` | POST | `finance.mpesa.stk_push.query` | `StkWorkspace` | ✅ | ✅ |
| `/finance/mpesa/stk-requests/<id>/identify/` | POST | `finance.mpesa.stk_push.reconcile` | `StkWorkspace` — was unwired, discovered and closed in the M-Pesa Operations Workspace slice | ✅ | ✅ |
| `/finance/mpesa/callbacks/` | GET | `finance.mpesa.callback.view` (server-side `status` filter honored — first real `FilterBar` consumer) | `CallbackWorkspace` | ✅ | ✅ |
| `/finance/mpesa/callbacks/<id>/` | GET | `finance.mpesa.callback.view` | `CallbackWorkspace` | ✅ | ✅ |
| `/finance/mpesa/callbacks/<id>/verify/` | POST | `finance.mpesa.callback.verify` | `CallbackWorkspace` | ✅ | ✅ |
| `/finance/mpesa/callbacks/<id>/reject/` | POST | `finance.mpesa.callback.verify` | `CallbackWorkspace` | ✅ | ✅ |
| `/finance/mpesa/callbacks/<id>/process/` | POST | `finance.mpesa.callback.process` | `CallbackWorkspace` | ✅ | ✅ |

## Attendance — Full

`/attendance/sessions/` (GET), `/attendance/sessions/open/` (POST), `/attendance/sessions/<id>/` (GET), `/attendance/sessions/<id>/records/` (POST), `/attendance/sessions/<id>/submit/` (POST) — all in `features/attendance.tsx`. ✅ Connected, ✅ Workflow complete, including opening a register via `/academics/class-groups/` (GET, `attendance.session.manage`, new — closes `ACADEMIC-GAP-01`; campus/`TeacherAssignment`-filtered to exactly what `open_attendance_session` would accept). Roster rows still show raw student UUIDs: `ACADEMIC-GAP-02`.

## Assessments — Full (one catalogue gap)

`/assessments/assessments/` (GET), `/assessments/assessments/<id>/` (GET), `/assessments/assessments/<id>/marks/` (POST), `/assessments/assessments/<id>/{submit,approve,reject,reopen,publish}/` (POST) — all in `features/assessments.tsx`. ✅ Connected, ✅ Workflow complete for marking/workflow transitions. Assessment *creation* has no UI: `ACADEMIC-GAP-04` (no term/class-group/subject catalogues). Marks-table rows show raw student UUIDs: `ACADEMIC-GAP-03`. `/assessments/students/<id>/summary/` — `StudentPage` Assessments tab, ✅/✅.

## Leave — Partial (workflow setup only; request lifecycle not yet wired)

`/leave/workflows/`, `/leave/workflows/<id>/stages/`, `/leave/workflows/<id>/stages/<id>/` — `features/leave.tsx` `LeaveWorkflowPage`. ✅/✅. `/leave/employees/<id>/balance/` — `EmployeePage` Leave tab, ✅/✅. `/leave/requests/` (list only, `pages.tsx` generic `resources` table) — Connected, **not** workflow complete (no submit/decide/withdraw/cancel UI). Backlog item #5.

## Students — Partial

`/students/` (list) — `StudentsPage`, ✅ Connected/✅ Workflow complete for what the endpoint actually offers: pagination only. The Student List Workspace slice removed the dead search box outright rather than leaving it disabled — `list_students` has no search/status filter and campus is derived from the caller's own membership, not a request parameter (`STUDENT-GAP-02`, expanded). No `GET /students/<id>/` retrieve at all (`STUDENT-GAP-01`) — `StudentPage` reads a `sessionStorage` cache from the list click instead. `/students/<id>/documents/` (GET/POST), `/students/<id>/documents/<id>/download/`, `/students/<id>/documents/<id>/` (DELETE) — ✅/✅ via `features/documents.tsx` `DocumentsPanel`. Guardians/Activity tabs: honest "not available" stub, zero backend API surface.

## Staff — Partial

`/staff/employees/` (list), `/staff/employees/<id>/` (detail), `/staff/employees/<id>/qualifications/` (read via `JsonPanel`) — `pages.tsx` `StaffPage`/`EmployeePage`, ✅ Connected/Partial workflow (list+detail+read work; no edit/status-change UI). `/staff/employees/<id>/documents/*` — now ✅/✅ via `DocumentsPanel` (this slice), **but see the campus-scope finding below** — list/download aren't campus-checked server-side the way upload/delete are. `/staff/employees/<id>/user-link/` — not wired (User Access tab shows a static message only).

## Documents — Full (this slice)

| Endpoint | Method | Permission | Frontend | Connected | Workflow complete |
|---|---|---|---|---|---|
| `/documents/setup/` | GET/PATCH | `documents.setup.view`/`.manage` | `pages.tsx` `SetupPage`'s Documents card | ✅ | ✅ |
| `/students/<id>/documents/` | GET/POST | `students.document.view`/`.manage` | `DocumentsPanel` in `StudentPage` | ✅ | ✅ |
| `/students/<id>/documents/<id>/download/` | GET | `students.document.view` | `DocumentsPanel` | ✅ | ✅ |
| `/students/<id>/documents/<id>/` | DELETE | `students.document.manage` | `DocumentsPanel` | ✅ | ✅ |
| `/staff/employees/<id>/documents/` | GET/POST | `staff.view`/`.manage` | `DocumentsPanel` in `EmployeePage` | ✅ | ✅ (backend campus-scope gap noted separately) |
| `/staff/employees/<id>/documents/<id>/download/` | GET | `staff.view` | `DocumentsPanel` | ✅ | ✅ (same caveat) |
| `/staff/employees/<id>/documents/<id>/` | DELETE | `staff.manage` | `DocumentsPanel` | ✅ | ✅ |

## Reporting — Full (this slice)

| Endpoint | Method | Permission | Frontend | Connected | Workflow complete |
|---|---|---|---|---|---|
| `/reports/catalogue/` | GET | (per-report, catalogue computes `can_view`/`can_export`) | `features/reporting.tsx` `ReportsPage` | ✅ | ✅ |
| `/reports/<code>/preview/` | GET | `reports.<group>.view` | `ReportWorkspace` | ✅ | ✅ |
| `/reports/<code>/export/` | POST | `reports.<group>.export` | `ReportWorkspace` | ✅ | ✅ |
| `/reports/exports/<id>/` | GET | (job scoped to tenant) | `ReportWorkspace` polling | ✅ | ✅ |
| `/reports/exports/<id>/download/` | GET | `reports.<group>.export` | `ReportWorkspace` | ✅ | ✅ |

`student_id`/`class_group_id`/`term_id`/`campus_id` parameters stay raw text/number inputs — no bounded catalogue exists for any of them (`STUDENT-GAP-02`, `ACADEMIC-GAP-01`/`-04`).

## Notifications — Full (this slice)

| Endpoint | Method | Permission | Frontend | Connected | Workflow complete |
|---|---|---|---|---|---|
| `/notifications/inbox/` | GET | membership only | `NotificationInboxPage` | ✅ | ✅ |
| `/notifications/inbox/<id>/read/` | POST | membership only | `NotificationInboxPage` | ✅ | ✅ |
| `/notifications/templates/` | GET/POST | `notifications.templates.view`/`.manage` | `NotificationTemplatesPage` | ✅ | ✅ |
| `/notifications/templates/<id>/` | PATCH | `notifications.templates.manage` | `NotificationTemplatesPage` | ✅ | ✅ (no delete endpoint exists) |
| `/notifications/setup/` | GET/PATCH | `notifications.setup.view`/`.manage` | `NotificationProvidersPage` | ✅ | ✅ |
| `/notifications/channels/<channel>/` | GET/PATCH | `notifications.setup.view`/`.manage` | `NotificationProvidersPage` | ✅ | ✅ |
| `/notifications/providers/` | GET/POST | `notifications.setup.view`/`.manage` | `NotificationProvidersPage` | ✅ | ✅ |
| `/notifications/rules/` | GET/POST | `notifications.rules.view`/`.manage` | `NotificationRulesPage` | ✅ | ✅ |
| `/notifications/rules/<id>/` | PATCH | `notifications.rules.manage` | `NotificationRulesPage` | ✅ | ✅ (no delete endpoint exists) |
| `/notifications/outbox/` | GET | `notifications.record.view` | `NotificationOutboxPage` | ✅ | ✅ |
| `/notifications/outbox/<id>/` | GET | `notifications.record.view` | `NotificationOutboxPage` drawer | ✅ | ✅ |
| `/notifications/outbox/<id>/retry/` | POST | `notifications.retry` | `NotificationOutboxPage` drawer | ✅ | ✅ |
| `/notifications/outbox/<id>/deliveries/` | GET | `notifications.record.view` | `NotificationOutboxPage` drawer | ✅ | ✅ |

No manual send/compose action exists anywhere — deliberate, matches the backend having no such endpoint.

## Academics — Full

`/academics/academic-years/`, `/academics/academic-levels/` — consumed only as pickers inside Finance, not their own page. ✅/✅ for that purpose.

## Not yet detailed (summary only, per the 2026-09-09 audit — exact endpoint list to be confirmed when scoped)

| Domain | Backend endpoints (approx.) | Current state | Backlog position |
|---|---|---|---|
| Tenancy Administration | 8 | 2 read-only (`/tenancy/roles/`, `/tenancy/memberships/` via generic `resources` table); role edit, permission catalogue, membership activate/deactivate, user invite all unwired | #2, next |
| Platform Admin | 9 | Module catalogue + tenant provisioning real (`PlatformHome`); plans/audit read-only; plan edit/delete, subscription edit, module overrides unwired | #3 |
| Timetable | 7 | 1 read-only (`/timetable/entries/` via `resources`); periods, entry CRUD, class/teacher schedule views unwired | #4 |
| Leave (request lifecycle) | 9 of 16 | See Leave section above | #5 |
| Admissions | 1 | `/admissions/enroll` only (RC Area 3) — CRUD/list deliberately absent by design, not a gap | N/A |
| Guardians | 0 | No backend API surface at all — honest "not available" stub in Student 360 | N/A |

## Backend finding logged, not yet fixed (frontend cannot compensate)

**Staff document campus scope**: `apps/staff/api.py`'s employee document list (`api.py:209-215`) and download (`api.py:234-247`) views never apply the campus-scoping check that upload (`services.py:173`) and delete (`services.py:193`) both apply. A campus-scoped staff viewer can list/download another campus's employee documents. Candidate RC Area 5 defect — recorded in project memory for when that RC area starts; not touched by this frontend slice, and the `DocumentsPanel` UI works correctly against the (currently too-permissive) API as-is.

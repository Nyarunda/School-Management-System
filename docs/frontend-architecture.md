# Frontend Architecture

## Stack (frozen decision)

React + TypeScript + Vite + Mantine + TanStack Query + React Router.

**Mantine is the UI framework throughout. DevExtreme is UX inspiration only** — we specifically decided not to install DevExtreme because of its commercial licensing. Where DevExtreme's enterprise-ERP UX ideas are worth borrowing (dense registers, sticky headers, resizable/hidden columns, server-side pagination/filtering/sorting, master-detail workspaces, strong status handling, operational grid layouts), they are built with Mantine primitives (`DataTable`, `ActionDialog`, `Badge`, `Modal`, etc.), never with a DevExtreme dependency.

```
React            component/application framework
TypeScript       type safety
Vite             build/dev tooling
Mantine          AppShell, tables, forms, modals/drawers, tabs, notifications, theme
TanStack Query   API/server-state management
React Router     routing
Tabler Icons     icons
```

## Target directory shape

```
frontend/src/
├── app/            App.tsx, router.tsx, navigation.ts, auth.tsx, access.ts, providers.tsx
├── api/            client.ts, errors.ts, pagination.ts, queryKeys.ts
├── components/     DataTable, ActionDialog, PageHeader, FilterBar, StatusBadge,
│                   RecordHeader, KeyValueGrid, EmptyState, ErrorState, LoadingState,
│                   PermissionGate
├── layouts/        ERPShell, PlatformShell, AuthLayout
├── features/       dashboard, students, admissions, attendance, assessments, timetable,
│                   finance, mpesa, staff, leave, communications, reports,
│                   administration, platform
├── theme/          theme.ts, tokens.ts, density.ts
├── hooks/, types/, utils/
└── main.tsx
```

A feature that outgrows a single file converts to a folder as it grows:

```
features/finance/
├── api.ts
├── types.ts
├── queries.ts
├── permissions.ts
├── pages/
│   ├── FeeStructuresPage.tsx
│   ├── FeeAssignmentsPage.tsx
│   ├── InvoicesPage.tsx / InvoiceDetailPage.tsx
│   ├── PaymentsPage.tsx / PaymentDetailPage.tsx
│   └── ReconciliationPage.tsx
└── components/
    ├── InvoiceStatusBadge.tsx
    ├── PaymentAllocationDialog.tsx
    └── PaymentReversalDialog.tsx
```

**Rule: do not restructure a single-file feature (`features/finance.tsx`, `features/mpesa.tsx`, etc.) into this folder shape pre-emptively.** Convert only once the file has genuinely outgrown being one file. Finance, M-Pesa, Leave, Attendance and Assessments have already been extracted out of the old monolithic `pages/pages.tsx` into single-file feature modules — that extraction is the current, correct state, not a stopping point that needs immediate further splitting.

## Sidebar / page architecture (target)

```
DASHBOARD
└── Dashboard

STUDENTS
├── Students — Student Directory, Student 360
└── Admissions — [future backend capability]

ACADEMICS
├── Attendance — Attendance Sessions, Attendance Register
├── Assessments — Assessment Register, Assessment Workspace, Marks Register
└── Timetable — Timetable, Schedule Workspace

FINANCE
├── Fee Structures, Fee Assignments
├── Invoices → Invoice Detail
├── Payments → Payment Detail
├── Incoming Payments, Reconciliation
└── M-Pesa — STK Requests, Callback Inbox

STAFF
├── Staff Directory, Employee 360
└── Leave — Leave Requests, Leave Balances, Workflow Setup

COMMUNICATION
├── Notifications, Templates, Delivery/Outbox (where supported)

REPORTS
├── Report Catalogue, Report Preview, Export Jobs

ADMINISTRATION
├── Users, Roles & Permissions, Module Setup, other supported tenant setup
```

Unsupported capability (Admissions CRUD beyond enroll, Guardians, campus administration, etc.) should not appear in the menu just to fill it out — the menu tracks what the backend actually supports.

**Operations vs. Administration** is a deliberate split: things users do every day (Students, Attendance, Assessments, Timetable, Invoices, Payments, M-Pesa, Staff, Leave Requests) vs. things administrators configure (Fee Structures, Leave Workflows, Roles, Permissions, Modules, Notification Templates, Tenant Setup). Not yet built as a distinct two-pane nav — today's `AppShell` (`components/Shell.tsx`) is one flat nav list — but this is the direction future nav work should move toward.

**Platform Admin is separate.** The SaaS-owner interface (`PlatformShell`) uses a different visual shell, different permissions, and does not carry the normal tenant `X-Tenant-Slug` behavior. This already exists (`components/Shell.tsx`'s `PlatformShell`, gated on `platformAccess` in `app/auth.tsx`) — keep it that way; don't fold platform admin into the tenant sidebar.

## Record workspaces (target, not yet built)

**Student 360** and **Employee 360** are the target "record workspace" pattern: a header (name, status, key identity facts, an actions menu) plus tabs, each tab's query firing only when that tab is opened (don't fetch the whole record on open — lazy-load per tab).

Student 360 target tabs: Overview, Academics, Attendance, Assessments, Fees, Payments, Documents. Guardians/Activity tabs stay unavailable until their backing APIs exist — don't fabricate them.

Employee 360 composes Leave/Documents/User-Access information but does not become the owner of those domains — domain ownership (Leave app owns leave data, Documents app owns document data) stays intact; Employee 360 is a view over them, permission-gated per section (e.g. `staff.user_link.manage` for the User Access tab).

## Operational register pages (target, not yet built)

Attendance and Assessments should each get a two-route shape: a list page and a workspace page (`/attendance` → session list, `/attendance/:sessionId` → register; `/assessments` → assessment list, `/assessments/:id` → workspace with Overview/Marks/Grading/Workflow/Amendment-History tabs, only exposing the transitions the backend actually supports: DRAFT → SUBMITTED → APPROVED → PUBLISHED, with REJECTED/REOPENED branches). Registers should be dense Mantine grids (adm. no / name / status / remarks), not generic list views. Timetable should render as a visual weekly grid, not an ordinary table — still Mantine, still server-driven data, DevExtreme borrowed only as a UX reference.

M-Pesa keeps its own routes separate from ordinary confirmed payments (`/m-pesa/stk`, `/m-pesa/stk/:id`, `/m-pesa/callbacks`, `/m-pesa/callbacks/:id`), and the callback detail view must keep the RECEIVED/UNTRUSTED → VERIFIED → PROCESSED security boundary visually obvious — this mirrors a real backend trust boundary (see `docs/rc/BACKEND_RC.md` Area 3/4), not just a UI nicety.

## Interaction & feedback standards

**Choosing modal vs. drawer vs. full workspace** — by operation type, not by habit:

| Operation | Interaction |
|---|---|
| Read / list / detail | Page, table, or a 360 workspace tab |
| Small create/edit operation | Mantine `Modal` (this codebase's `ActionDialog`) |
| Record investigation or larger context | Drawer, or a dedicated workspace |
| Destructive / irreversible action | Confirmation `Modal` |
| State transition where the consequence matters | Confirmation `Modal` |

Don't turn every operation into a modal — dense workflows (Attendance registers, Assessment marks entry, Timetable scheduling, 360 workspaces) deserve full operational screens, not dialogs. As of this audit, the existing modal/workspace choices already match this: `ActionDialog` is used only for small create/edit/confirm actions (approve a fee structure, allocate/reverse a payment, record a payment, verify/reject an M-Pesa callback, add a workflow stage), while `RegisterWorkspace` (Attendance) and `AssessmentWorkspace` (Assessments) are already full operational screens for the dense roster/marks-entry interactions. Keep new work consistent with this split rather than defaulting everything to a dialog.

**Feedback on every mutation** — one centralized abstraction, `frontend/src/components/notifications/notify.ts` (`notify.success(message)` / `notify.error(message, error?)` / `notify.warning(message)` / `notify.info(message)`), wrapping `@mantine/notifications`. This is the *only* place `notifications.show(...)` is called anywhere in the codebase — never call it directly from a feature file, and never construct a second one-off toast/sound mechanism. Rules:
- Toasts render bottom-center (`<Notifications position="bottom-center"/>` in `main.tsx`).
- Every mutation calls `notify.success(...)` in `onSuccess` and `notify.error(...)` in `onError`, **in addition to**, not instead of, the existing inline field-level error display (each feature file's local `Failure` component) — inline stays the detailed/field-level error, the toast is the at-a-glance outcome.
- Messages are domain-specific and match the actual action ("Payment recorded successfully", "Invoice issued", "Fee structure approved") — never generic ("Success!", "Something went wrong.").
- `notify.error` automatically overrides the message to "You do not have permission to perform this action" whenever the underlying error is a 403 (`ApiError` with `status === 403`), regardless of what the call site passed in.
- Sound (`frontend/src/components/notifications/notificationSound.ts`) plays only on success/error (never on ordinary queries, page loads, or warning/info toasts), is a short WebAudio oscillator tone (no audio asset files), is **off by default**, and is user-toggleable (the sidebar-foot "Sound on/off" button in `components/Shell.tsx`, mirroring the existing density toggle). Never assume sound is available or audible — it fails silently if the browser blocks `AudioContext`.

## Styling standard

**Mantine is the authoritative UI and styling system.** Priority order: Mantine component props → Mantine layout primitives (`Group`/`Stack`/`Grid`/`Flex`/`Box`) → Mantine theme → Mantine style props → custom CSS, only when Mantine genuinely cannot express the behavior (dense registers, the timetable grid, sticky/resizable table internals). Do not introduce custom CSS for spacing, typography, colour, borders, radius, alignment, or standard form/modal/badge/card layout — those come from Mantine. Do not introduce another component framework to solve a styling problem. Use Tabler Icons (already installed, `@tabler/icons-react`) for any new icon rather than adding to `ui.tsx`'s hand-drawn glyph set.

This applies going forward to new work — `finance.tsx`/`mpesa.tsx`/`attendance.tsx`/`assessments.tsx`/`leave.tsx` (plain HTML `<input>`/`<select>` + custom CSS classes) are **not** retrofitted just to comply; they already work. `notifications.tsx`, `documents.tsx`, and `reporting.tsx` are the first features built to this standard and are the reference examples.

## Coverage-audit backlog (frontend/backend API wiring)

The full endpoint-by-endpoint ledger lives in **`docs/frontend-api-coverage.md`** — that's the authoritative, kept-current record; this section only tracks sequencing. Completion bar for each domain: **"endpoint connected" is not "workflow complete"** — a report catalogue `GET` reaching a table isn't Reporting done if preview/export/download have no UI.

1. ~~**Notifications + Documents + Reporting**~~ — done (2026-09-09). Five Notifications workspaces (`features/notifications.tsx`), a shared `DocumentsPanel` (`features/documents.tsx`) wired into Student/Employee 360 replacing the broken `JsonPanel`-on-a-paginated-list bug, and a full Reporting workspace (`features/reporting.tsx`) preserving the async preview→export→poll→download model. See `docs/frontend-api-coverage.md` for the row-level detail.
2. **Tenancy Administration** (permission catalogue, role create/edit, membership activate/deactivate, user invite) — blocks normal operation today (nobody can invite a user or edit a role's permissions from the app). Next up.
3. **Platform Admin remainder** (plan edit/delete, tenant subscription edit, module overrides, per-tenant audit).
4. **Timetable** (periods + entry CRUD, class/teacher schedule views).
5. **Leave request lifecycle** (submit/decide/withdraw/cancel) — backend fully built and tested, contract already documented in the coverage-audit plan history.
6. **Documents CRUD** — done as part of item 1 above (folded in early since Documents shared no dedicated feature file of its own).

Do not invent an endpoint or fabricate data to make a domain look complete — where the backend genuinely has no contract (Guardians has zero API surface; Attendance/Assessment creation needs catalogue endpoints that don't exist; no bounded student search/lookup exists at all — `STUDENT-GAP-02`), record it in `docs/frontend-backend-contract-gaps.md` instead.

## What this doc is not

This is direction for future frontend work, not a task list executed all at once. Each piece above gets its own scoped plan when it's actually picked up — see `docs/frontend-backend-contract-gaps.md` for backend API gaps already blocking specific pieces of this (class-group catalogue, attendance/assessment roster identity, assessment reference catalogues).

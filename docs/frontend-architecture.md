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

## What this doc is not

This is direction for future frontend work, not a task list executed all at once. Each piece above gets its own scoped plan when it's actually picked up — see `docs/frontend-backend-contract-gaps.md` for backend API gaps already blocking specific pieces of this (class-group catalogue, attendance/assessment roster identity, assessment reference catalogues).

# Engineering baseline

Reviewed twice on 2026-09-06 against the local source; this revision includes the academics and student API additions. This is an assessment, not a claim of production readiness.

## Product and structure

The product is a multi-school management platform. Each school is a Tenant; users access schools through Membership records with a tenant-owned Role and optional Campus. The backend is a Django modular monolith with tenancy, admissions, students, guardians, activity, and academics apps. Migrations exist for all six apps.

Admissions has an explicit status graph and a service that creates a student from an accepted application. Students have lifecycle transitions and campus placement. Guardians link to students. ActivityEvent records domain actions; a separate AuditEvent model exists but the domain services do not populate it. Document models currently hold metadata, not file storage workflows.

The React/TypeScript frontend is a student workspace with a list, detail tabs, and admission modal. It uses local component state and sample records; refresh loses admissions. Attendance, averages, academic placement/history, and activity text are placeholders. The backend now exposes GET `/api/v1/students/`, but the frontend does not call it.

Academics models school configuration, years, terms, levels, classes, subjects, teacher assignments, and annual student enrollments. Admission enrollment creates the student identity; academic enrollment separately places that student in a year and class. A unique constraint permits one academic enrollment per school/student/year, so term-by-term placements within one year are not currently represented as separate enrollments.

## Second-pass request and write tracing

- Student listing requires authentication, an active school membership, and `students.view` (superusers bypass the permission string but still need membership here). Missing tenant context returns 404; denied access returns 403. Page size defaults to 25 and is capped at 100. The selector scopes students, joins campus, limits selected fields, and orders by admission number.
- The API test asserts three queries using forced authentication: membership resolution, pagination count, and student retrieval. This does not establish the full query count for session authentication: middleware separately resolves the school and membership. Fixed query count also does not prove bounded count/offset query cost at large tenant sizes.
- Academic enrollment receives explicit user and tenant and checks `academics.students.enroll`. It locks the student using an unscoped primary-key lookup before checking that student's tenant. On PostgreSQL this can lock another tenant's row before rejecting it. It also serializes enrollment work for the same student across different years.
- Academic enrollment and its activity event share an outer transaction. The inner savepoint catches every IntegrityError as duplicate enrollment, potentially mislabeling other integrity failures.
- Same-school checks do not establish academic consistency: term/year, class/level, and class/campus agreement are unchecked. Date ordering and a single current academic year are also not constrained.
- The shared permission helper bypasses membership for superusers and does not check active tenant status. Its policy differs from the student endpoint. Existing admission, student lifecycle, and guardian services do not use this helper.
- Student list campus joins rely on valid stored relationships; a malformed cross-school student/campus link could expose the foreign campus name despite scoping the student itself.
- The academic enrollment index on tenant/student/year duplicates the column sequence of its unique constraint; evaluate the extra index before retaining its write/storage cost.

## Findings from source

- Tenant filtering is opt-in through `for_tenant()`. Unscoped queries remain available. ContextVar tenant resolution does not automatically scope the ORM.
- Middleware verifies active school membership when a tenant header is supplied, but unresolved tenant requests continue. Future protected endpoints must reject missing or unauthorized tenant context.
- Domain services generally derive the tenant from a supplied object rather than accepting an independently authorized tenant. Actor membership and role permissions are not enforced there.
- Cross-tenant relationship checks cover selected service paths. Model `clean()` methods are not database constraints, and direct writes can bypass those checks. Several relationships lack equivalent checks entirely.
- Enrollment is atomic but trusts the supplied application's status. Stale accepted instances can create multiple students with different admission numbers. There is no unique application-to-student relationship or idempotency mechanism.
- Application and student transitions also trust in-memory state, allowing stale updates. Application transitions permit ACCEPTED to ENROLLED without creating a student.
- Most domain mutations and activity inserts are separate transactions; an activity failure can leave a successful mutation without its event.
- Django now selects PostgreSQL with `DB_ENGINE=postgres`, and Compose supplies that setting and credentials. Local execution defaults to SQLite. Redis remains unused. The backend runs the development server. Compose has no database readiness health check. No production availability, failover, observability, or deployment workflow is present; `infra/` is empty.
- Frontend dependencies use `latest`, there is no lockfile, and React type packages are not declared. The frontend build has not been verified.
- CSS contains duplicated global rules and trailing overrides that narrow the workspace and enlarge the title. The modal lacks explicit dialog semantics and focus handling.
- Architecture documentation's next slice is stale: middleware, audit models, and onboarding services already exist.

## Working direction

The user's priorities are strict multi-tenant isolation, high traffic capacity, deployment continuity, minimal blocking, and a polished interface with deliberate visual design.

First establish enforceable tenant and authorization boundaries and correct concurrent writes, including consistent academic relationships. Then connect one complete admissions-to-student workflow to the frontend. Keep the modular monolith while workload evidence supports it.

For concurrency, favor conditional updates, database uniqueness, idempotent operations, and short transactions. Interpret the nonblocking requirement as avoiding unnecessary contention; database writes still acquire locks. Validate behavior against PostgreSQL before making throughput claims.

Availability work should define measurable service targets, health checks, graceful draining, compatible schema changes, rollback, and tested recovery. Traffic volume, peak concurrency, school sizes, latency targets, and recovery objectives are still unspecified.

UI work should prioritize clear school/campus context, readable lists, restrained typography, consistent spacing, keyboard access, and accurate loading, empty, error, and success states. Replace fabricated metrics with actual data or honest empty states.

## Verification

The second review ran 25 tests successfully on SQLite using explicit module labels, including `apps.students.api_tests` and `apps.academics.tests`. Migration drift check reports no changes. The first review found app-package test labels fail discovery with a namespace-package path error; explicit module labels remain the verification entry point. The API test file is named `api_tests.py`, so do not assume default `test*.py` discovery includes it. No PostgreSQL concurrency, load, deployment, frontend build, or browser validation has been performed during these reviews.

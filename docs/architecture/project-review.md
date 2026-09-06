# Engineering baseline

Reviewed 2026-09-06 against the local source. This is an assessment, not a claim of production readiness.

## Product and structure

The product is a multi-school management platform. Each school is a Tenant; users access schools through Membership records with a tenant-owned Role and optional Campus. The backend is a Django modular monolith with tenancy, admissions, students, guardians, and activity apps. Initial migrations exist for all five apps.

Admissions has an explicit status graph and a service that creates a student from an accepted application. Students have lifecycle transitions and campus placement. Guardians link to students. ActivityEvent records domain actions; a separate AuditEvent model exists but the domain services do not populate it. Document models currently hold metadata, not file storage workflows.

The React/TypeScript frontend is a student workspace with a list, detail tabs, and admission modal. It uses local component state and sample records; refresh loses admissions. Attendance, averages, and activity text are placeholders. The backend URL configuration is empty, so there is no end-to-end API workflow.

## Findings from source

- Tenant filtering is opt-in through `for_tenant()`. Unscoped queries remain available. ContextVar tenant resolution does not automatically scope the ORM.
- Middleware verifies active school membership when a tenant header is supplied, but unresolved tenant requests continue. Future protected endpoints must reject missing or unauthorized tenant context.
- Domain services generally derive the tenant from a supplied object rather than accepting an independently authorized tenant. Actor membership and role permissions are not enforced there.
- Cross-tenant relationship checks cover selected service paths. Model `clean()` methods are not database constraints, and direct writes can bypass those checks. Several relationships lack equivalent checks entirely.
- Enrollment is atomic but trusts the supplied application's status. Stale accepted instances can create multiple students with different admission numbers. There is no unique application-to-student relationship or idempotency mechanism.
- Application and student transitions also trust in-memory state, allowing stale updates. Application transitions permit ACCEPTED to ENROLLED without creating a student.
- Most domain mutations and activity inserts are separate transactions; an activity failure can leave a successful mutation without its event.
- Django uses SQLite even though Compose starts PostgreSQL. Redis is provisioned but unused. The backend runs the development server. No production availability, failover, observability, or deployment workflow is present; `infra/` is empty.
- Frontend dependencies use `latest`, there is no lockfile, and React type packages are not declared. The frontend build has not been verified.
- CSS contains duplicated global rules and trailing overrides that narrow the workspace and enlarge the title. The modal lacks explicit dialog semantics and focus handling.
- Architecture documentation's next slice is stale: middleware, audit models, and onboarding services already exist.

## Working direction

The user's priorities are strict multi-tenant isolation, high traffic capacity, deployment continuity, minimal blocking, and a polished interface with deliberate visual design.

First establish enforceable tenant and authorization boundaries and correct concurrent writes. Then connect one complete admissions-to-student workflow to the frontend. Keep the modular monolith while workload evidence supports it.

For concurrency, favor conditional updates, database uniqueness, idempotent operations, and short transactions. Interpret the nonblocking requirement as avoiding unnecessary contention; database writes still acquire locks. Validate behavior against PostgreSQL before making throughput claims.

Availability work should define measurable service targets, health checks, graceful draining, compatible schema changes, rollback, and tested recovery. Traffic volume, peak concurrency, school sizes, latency targets, and recovery objectives are still unspecified.

UI work should prioritize clear school/campus context, readable lists, restrained typography, consistent spacing, keyboard access, and accurate loading, empty, error, and success states. Replace fabricated metrics with actual data or honest empty states.

## Verification

All 12 existing tests pass on SQLite using explicit `.tests` module labels. Migration drift check reports no changes. App-package test labels fail discovery with a namespace-package path error; explicit `.tests` module labels are the working verification entry point. No load, concurrency, deployment, frontend build, or browser validation has been performed during this review.

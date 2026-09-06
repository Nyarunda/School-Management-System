# Execution Model

The School ERP is sync-first.

1. Django and DRF request handlers remain synchronous by default.
2. ORM-heavy CRUD and financial services remain synchronous.
3. Database transactions contain database work only.
4. External HTTP, SMS, email, PDF generation, and other slow work do not run while database locks are held.
5. Long-running or retryable work belongs in Celery.
6. Redis is queue/cache infrastructure, never the source of financial truth.
7. Async is introduced only for demonstrated concurrent external I/O or long-lived connections such as WebSockets, SSE, or streaming.
8. Async/sync boundaries must be explicit and tested.
9. Querysets are fully prepared with `select_related`, `prefetch_related`, or annotations before serializer access.
10. PostgreSQL is the transactional source of truth.

Changing a normal endpoint to `async def` is not a performance strategy. A proposal for async code must identify the blocking I/O it removes, the boundary it crosses, and the tests that prove the benefit without weakening transaction behavior.

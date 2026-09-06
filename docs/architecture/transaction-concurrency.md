# Transaction Concurrency Standard

Transactions protect invariants; they are not a general-purpose wrapper for unrelated work.

- Keep transactions short and deterministic.
- Lock only the rows required by the invariant.
- Acquire multiple locks in stable primary-key order.
- Never perform HTTP, SMS, email, PDF generation, or other external work while locks are held.
- Use database uniqueness and check constraints to arbitrate concurrent writes.
- Translate only known transient or constraint conflicts; propagate unrelated database errors.
- Use `select_for_update()` deliberately, not as a default query modifier.
- Use `skip_locked` for queue-style workers where dropping already-claimed work from a polling batch is acceptable.
- Run concurrency tests against PostgreSQL. SQLite tests do not establish production locking behavior.
- Record activity/outbox data in the same transaction when the event is part of the business invariant; dispatch external side effects after commit.

Every new financial command should document its invariant, lock order, transaction boundary, conflict behavior, and PostgreSQL test coverage.

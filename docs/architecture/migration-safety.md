# Safe Migration Standard

Database migrations are production architecture. A migration must be reviewed for lock duration, table size, write volume, rollback behavior, and compatibility with rolling application deployments.

## Risk classification

Every production migration receives a risk classification in its pull request or release notes:

- **LOW**: new table, small setup-table change, or metadata-only operation.
- **MEDIUM**: new nullable column, constraint change, or moderate-table index.
- **HIGH**: changes to hot or large tables, NOT NULL changes, backfills, indexes on transactional tables, foreign keys on large tables, column rename/removal, and data type changes.

Finance, payments, ledger, activity, audit, attendance, and notification tables should be presumed high risk until their production size and write profile demonstrate otherwise.

## Required SQL review

For every non-trivial migration, review the SQL Django will execute:

```powershell
python manage.py makemigrations --check
python manage.py migrate --plan
python manage.py sqlmigrate <app_label> <migration_number>
```

Review explicitly for `ALTER TABLE`, `CREATE INDEX`, `DROP COLUMN`, `SET NOT NULL`, foreign keys, table rewrites, large updates, and operations that acquire long-lived locks. A migration is not approved from Python model code alone.

## Expand, migrate, contract

Changes to existing or continuously written tables use a backward-compatible sequence:

1. **Expand**: add nullable or additive schema; deploy code that understands both old and new shapes.
2. **Migrate**: backfill in bounded, restartable batches; make progress observable and throttleable.
3. **Contract**: after all application instances use the new shape and validation confirms completeness, remove obsolete columns or tighten constraints in a later release.

Rolling deployments must never require old application instances to understand a schema that only the new release supports.

## Backfills

Never put a potentially large backfill into one deployment transaction. Prefer a restartable management command or background job that:

- processes bounded batches;
- commits each batch;
- records progress and can resume safely;
- avoids holding locks while doing unrelated work;
- can be throttled or stopped without leaving ambiguous state.

## Indexes and constraints

For hot PostgreSQL tables, consider concurrent index creation when supported. Django concurrent index operations require a non-atomic migration and must be planned for operational execution. The migration review must account for the additional disk, CPU, write, and cleanup costs of indexes.

Database uniqueness and check constraints should enforce business invariants where possible. Application services provide useful errors and workflows, but they do not replace database enforcement under concurrency.

## CI/CD and release gates

Migration-related changes must pass:

- Python and tenant-isolation tests;
- query and concurrency regression tests where affected;
- `python manage.py makemigrations --check`;
- migration plan and generated SQL review for medium/high-risk changes;
- container build checks;
- PostgreSQL staging migration and smoke tests;
- health checks after migration.

Staging success does not prove production safety. Release approval must consider production-like table sizes, indexes, lock behavior, write volume, and rollback or forward-fix strategy.

## Definition of done

A database change is complete only when its risk is classified, generated SQL is reviewed, deployment compatibility is documented, backfill behavior is restartable where needed, constraints and indexes are intentional, and the affected PostgreSQL tests pass. Payment and ledger schema changes additionally require a concurrency and migration rollback or forward-fix note.

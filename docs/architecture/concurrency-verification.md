# Authorization and academic enrollment follow-up

The shared membership check now requires an active user, active school, active membership, and a role belonging to that school. Superusers still require membership but may bypass individual role permissions. Student listing and tenant middleware use the same helper as academic and finance service authorization. Older admission, guardian, and student mutation services still need explicit authorization contracts before exposure as write APIs.

Academic enrollment reloads each supplied record through the authorized tenant scope. It validates term/year, class/level, and class/campus agreement using stored records. The model's `clean()` provides the same relationship checks for callers that explicitly validate models; direct ORM writes do not automatically invoke it.

Enrollment no longer explicitly locks the student. The existing unique constraint on tenant/student/year arbitrates competing inserts. Enrollment and activity recording commit together. Only that uniqueness violation is converted to a duplicate-enrollment validation error; unrelated integrity failures propagate. Database foreign-key and uniqueness checks still acquire locks.

## PostgreSQL verification

Verified locally on 2026-09-06: all 45 tests passed on an isolated PostgreSQL 17.6 instance with Django 5.2.5 and the available psycopg2 2.9.10 driver. This includes all three concurrent transaction tests. An earlier SQLite regression run passed 40 tests before the final API tests were added. Migration drift and whitespace checks passed. The declared psycopg 3 dependency was not available in this environment; its driver-specific execution remains unverified.

Use a dedicated test PostgreSQL instance with a role allowed to create test databases. In PowerShell, set `DB_ENGINE=postgres` and `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` for that instance, then run from `backend/`:

```powershell
python manage.py test apps.tenancy.tests apps.admissions.tests apps.students.tests apps.students.api_tests apps.guardians.tests apps.academics.tests apps.academics.test_concurrency apps.finance.tests --noinput
python manage.py makemigrations --check --dry-run
```

The concurrency tests use separate thread-local connections and real transactions, synchronize immediately before enrollment inserts, and bound database waits. They check:

- Competing inserts for the same student/year leave exactly one enrollment and activity event.
- Enrollments for different years can both reach insertion without an explicit student lock serializing them.
- A foreign school's locked student is rejected without waiting for that lock.

Concurrency tests skip on SQLite. Passing SQLite tests alone is not evidence of PostgreSQL concurrency correctness. The API module is explicitly listed because its `api_tests.py` filename is not matched by default test discovery.

## Remaining boundaries

These tests do not establish throughput or downtime guarantees. Simultaneous edits to academic setup after relationship validation require a separate immutability/versioning or database constraint policy. Financial lock acquisition, admission enrollment concurrency, database-enforced cross-tenant foreign keys, and zero-downtime deployment remain separate follow-up work. No schema migration is required for this change.

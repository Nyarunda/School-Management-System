#!/usr/bin/env bash
# Backend RC Area 1 evidence: migrate-from-zero, pending-migration hygiene,
# and migrate-forward (old schema + real data -> current HEAD) against a
# disposable PostgreSQL 17 instance. Plain git/docker/psql -- no dependency
# on any agent/session-specific tooling.
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
REPO_ROOT="$(pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"
SOURCE_COMMIT="12ba0c7"
TARGET_COMMIT="$(git rev-parse HEAD)"
TARGET_SHORT="$(git rev-parse --short HEAD)"
RUN_ID="$$"
PG_NAME="school-rc-${RUN_ID}-migpg"
DB_ZERO="school_management_zero"
DB_FORWARD="school_management_forward"
PG_PASSWORD="rc-migration-password"
HOST_PORT="18432"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_DIR="${REPO_ROOT}/docs/rc/evidence"
EVIDENCE_FILE="${EVIDENCE_DIR}/area1-migration-gate-${TARGET_SHORT}-${TIMESTAMP}.txt"
WORKTREE_DIR="$(mktemp -d)"
mkdir -p "${EVIDENCE_DIR}"

PASS=1
log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "${EVIDENCE_FILE}"; }
assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [[ "${expected}" == "${actual}" ]]; then
        log "PASS: ${desc} (got ${actual})"
    else
        log "FAIL: ${desc} (expected ${expected}, got ${actual})"
        PASS=0
    fi
}
psql_exec() { docker exec -e PGPASSWORD="${PG_PASSWORD}" "${PG_NAME}" psql -U school_management -h localhost -tA -d "$1" -c "$2"; }

cleanup() {
    log "Cleaning up: removing worktree and disposable postgres container."
    git worktree remove --force "${WORKTREE_DIR}" >/dev/null 2>&1 || true
    docker rm -f "${PG_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

: > "${EVIDENCE_FILE}"
log "Backend RC Area 1 migration gate"
log "Source commit (pre-existing schema+data baseline): ${SOURCE_COMMIT}"
log "Target commit (RC candidate under test): ${TARGET_COMMIT}"

docker run -d --name "${PG_NAME}" -p "${HOST_PORT}:5432" \
    -e POSTGRES_USER=school_management -e POSTGRES_PASSWORD="${PG_PASSWORD}" -e POSTGRES_DB=school_management \
    postgres:17-alpine >/dev/null
for _ in $(seq 1 30); do
    docker exec "${PG_NAME}" pg_isready -U school_management >/dev/null 2>&1 && break
    sleep 1
done
docker exec "${PG_NAME}" psql -U school_management -d school_management -c "CREATE DATABASE ${DB_ZERO}" >/dev/null
docker exec "${PG_NAME}" psql -U school_management -d school_management -c "CREATE DATABASE ${DB_FORWARD}" >/dev/null

COMMON_DB_ENV=(DB_ENGINE=postgres POSTGRES_HOST=localhost POSTGRES_PORT="${HOST_PORT}" POSTGRES_USER=school_management POSTGRES_PASSWORD="${PG_PASSWORD}")

log "--- Step 1: migrate from zero (current HEAD, ${DB_ZERO}) ---"
if (cd "${BACKEND_DIR}" && env "${COMMON_DB_ENV[@]}" POSTGRES_DB="${DB_ZERO}" python manage.py migrate --noinput >>"${EVIDENCE_FILE}" 2>&1); then
    log "PASS: migrate from zero succeeded"
else
    log "FAIL: migrate from zero failed"
    PASS=0
fi

log "--- Step 2: check --deploy against a real production-shaped env ---"
DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
FIELD_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
PROD_ENV=(
    DJANGO_ENV=production DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY}" FIELD_ENCRYPTION_KEY="${FIELD_ENCRYPTION_KEY}"
    PUBLIC_BASE_URL=https://school.example DJANGO_ALLOWED_HOSTS=school.example
    DJANGO_CACHE_URL=redis://localhost:6379/2
    "${COMMON_DB_ENV[@]}" POSTGRES_DB="${DB_ZERO}"
)
if (cd "${BACKEND_DIR}" && env "${PROD_ENV[@]}" python manage.py check --deploy >>"${EVIDENCE_FILE}" 2>&1); then
    log "PASS: check --deploy is clean under real production env"
else
    log "FAIL: check --deploy raised issues under real production env"
    PASS=0
fi

log "--- Step 3: pending-migration hygiene ---"
makemigrations_output="$(cd "${BACKEND_DIR}" && env "${COMMON_DB_ENV[@]}" POSTGRES_DB="${DB_ZERO}" python manage.py makemigrations --check --dry-run 2>&1)" && makemigrations_rc=0 || makemigrations_rc=$?
echo "${makemigrations_output}" >>"${EVIDENCE_FILE}"
assert_eq "makemigrations --check --dry-run exits 0 (no model changes missing a migration)" "0" "${makemigrations_rc}"
if (cd "${BACKEND_DIR}" && env "${COMMON_DB_ENV[@]}" POSTGRES_DB="${DB_ZERO}" python manage.py migrate --check >>"${EVIDENCE_FILE}" 2>&1); then
    log "PASS: migrate --check confirms all migrations are applied to ${DB_ZERO}"
else
    log "FAIL: migrate --check found unapplied migrations on ${DB_ZERO}"
    PASS=0
fi

log "--- Step 4: migrate forward (old schema+data at ${SOURCE_COMMIT} -> current HEAD, ${DB_FORWARD}) ---"
git worktree add --detach "${WORKTREE_DIR}" "${SOURCE_COMMIT}" >>"${EVIDENCE_FILE}" 2>&1
OLD_PROVISION_CMD="${WORKTREE_DIR}/backend/apps/finance/management/commands/loadtest_provision.py"
if [[ ! -f "${OLD_PROVISION_CMD}" ]]; then
    log "FAIL: loadtest_provision.py not found at ${SOURCE_COMMIT} -- cannot seed a representative pre-existing database"
    PASS=0
else
    log "Confirmed loadtest_provision.py exists at ${SOURCE_COMMIT}"
    (cd "${WORKTREE_DIR}/backend" && env "${COMMON_DB_ENV[@]}" POSTGRES_DB="${DB_FORWARD}" python manage.py migrate --noinput >>"${EVIDENCE_FILE}" 2>&1)
    (cd "${WORKTREE_DIR}/backend" && env "${COMMON_DB_ENV[@]}" POSTGRES_DB="${DB_FORWARD}" python manage.py loadtest_provision --tenants 3 --students-per-tenant 5 >>"${EVIDENCE_FILE}" 2>&1)

    CHECKSUM_SQL="SELECT md5(string_agg(t, ',' ORDER BY t)) FROM (
        SELECT slug::text AS t FROM tenancy_tenant
        UNION ALL SELECT (tenant_id::text || ':' || user_id::text || ':' || role_id::text || ':' || is_active::text) FROM tenancy_membership
        UNION ALL SELECT (id::text || ':' || tenant_id::text) FROM students_student
        UNION ALL SELECT (id::text || ':' || total::text || ':' || status::text) FROM finance_invoice
    ) x;"
    checksum_before="$(psql_exec "${DB_FORWARD}" "${CHECKSUM_SQL}" | tr -d '[:space:]')"
    log "Pre-migration data checksum (${DB_FORWARD}): ${checksum_before}"

    if (cd "${BACKEND_DIR}" && env "${COMMON_DB_ENV[@]}" POSTGRES_DB="${DB_FORWARD}" python manage.py migrate --noinput >>"${EVIDENCE_FILE}" 2>&1); then
        log "PASS: forward migration (current HEAD onto ${SOURCE_COMMIT}'s populated schema) succeeded"
    else
        log "FAIL: forward migration failed"
        PASS=0
    fi

    authtoken_table="$(psql_exec "${DB_FORWARD}" "SELECT to_regclass('authtoken_token') IS NOT NULL;" | tr -d '[:space:]')"
    assert_eq "authtoken_token table exists after forward migration" "t" "${authtoken_table}"

    checksum_after="$(psql_exec "${DB_FORWARD}" "${CHECKSUM_SQL}" | tr -d '[:space:]')"
    log "Post-migration data checksum (${DB_FORWARD}): ${checksum_after}"
    assert_eq "representative data unchanged by forward migration" "${checksum_before}" "${checksum_after}"
fi

log ""
log "Source commit: ${SOURCE_COMMIT}  |  Target commit: ${TARGET_COMMIT}"
if [[ "${PASS}" -eq 1 ]]; then
    log "RESULT: ALL CHECKS PASSED"
    exit 0
else
    log "RESULT: ONE OR MORE CHECKS FAILED -- see above"
    exit 1
fi

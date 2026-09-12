#!/usr/bin/env bash
# Backend RC Area 1 evidence: proves the production Docker image actually
# boots, serves traffic, reports the correct health/readiness semantics
# under dependency failure, and shuts down gracefully on SIGTERM.
#
# This formalizes what Milestone 22.3 verified manually (a one-off build/
# run/curl/stop pass that caught the missing-`exec` PID-1 bug in the
# Dockerfile). No dependency on any agent/session-specific tooling --
# plain docker + curl + python, runnable by any developer or future CI.
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
REPO_ROOT="$(pwd)"
SHORT_SHA="$(git rev-parse --short HEAD)"
RUN_ID="$$"
IMAGE_TAG="school-management-backend:rc-${SHORT_SHA}"
NETWORK="school-rc-${RUN_ID}-net"
PG_NAME="school-rc-${RUN_ID}-pg"
REDIS_NAME="school-rc-${RUN_ID}-redis"
APP_NAME="school-rc-${RUN_ID}-app"
HOST_PORT="18080"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_DIR="${REPO_ROOT}/docs/rc/evidence"
EVIDENCE_FILE="${EVIDENCE_DIR}/area1-docker-smoke-${SHORT_SHA}-${TIMESTAMP}.txt"
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
json_field() { python -c "import json,sys; print(json.load(sys.stdin).get('$1', ''))"; }
# The app trusts X-Forwarded-Proto from its reverse proxy (BEHIND_REVERSE_PROXY,
# config/settings.py) -- a bare curl from the host is standing in for that
# proxy, so it must send the same header a real one would, or SECURE_SSL_REDIRECT
# 301s every request.
curl_app() { curl -s -H 'X-Forwarded-Proto: https' "$@"; }

cleanup() {
    log "Cleaning up containers/network (image ${IMAGE_TAG} is kept)."
    docker rm -f "${APP_NAME}" "${PG_NAME}" "${REDIS_NAME}" >/dev/null 2>&1 || true
    docker network rm "${NETWORK}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

: > "${EVIDENCE_FILE}"
log "Backend RC Area 1 Docker smoke test"
log "Git commit: $(git rev-parse HEAD)"
log "Docker version: $(docker --version)"

log "Building image ${IMAGE_TAG}"
docker build -t "${IMAGE_TAG}" backend >>"${EVIDENCE_FILE}" 2>&1

docker network create "${NETWORK}" >/dev/null
log "Starting postgres:17-alpine and redis:7-alpine on isolated network ${NETWORK}"
docker run -d --name "${PG_NAME}" --network "${NETWORK}" \
    -e POSTGRES_DB=school_management -e POSTGRES_USER=school_management -e POSTGRES_PASSWORD=rc-smoke-password \
    postgres:17-alpine >/dev/null
docker run -d --name "${REDIS_NAME}" --network "${NETWORK}" redis:7-alpine >/dev/null

log "Waiting for postgres readiness"
for _ in $(seq 1 30); do
    docker exec "${PG_NAME}" pg_isready -U school_management >/dev/null 2>&1 && break
    sleep 1
done

DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
FIELD_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
COMMON_ENV=(
    -e DJANGO_ENV=production -e DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY}" -e FIELD_ENCRYPTION_KEY="${FIELD_ENCRYPTION_KEY}"
    -e PUBLIC_BASE_URL=https://school.example -e DJANGO_ALLOWED_HOSTS=school.example,localhost
    -e DB_ENGINE=postgres -e POSTGRES_DB=school_management -e POSTGRES_USER=school_management
    -e POSTGRES_PASSWORD=rc-smoke-password -e POSTGRES_HOST="${PG_NAME}"
    -e DJANGO_CACHE_URL="redis://${REDIS_NAME}:6379/2"
)

log "Running migrate against the fresh database"
docker run --rm --network "${NETWORK}" "${COMMON_ENV[@]}" "${IMAGE_TAG}" python manage.py migrate --noinput >>"${EVIDENCE_FILE}" 2>&1

log "Starting the application container"
docker run -d --name "${APP_NAME}" --network "${NETWORK}" -p "${HOST_PORT}:8000" "${COMMON_ENV[@]}" "${IMAGE_TAG}" >/dev/null

log "Waiting for the app to accept connections"
for _ in $(seq 1 30); do
    curl_app -f "http://localhost:${HOST_PORT}/healthz/" >/dev/null 2>&1 && break
    sleep 1
done

log "--- Baseline: everything healthy ---"
healthz_code="$(curl_app -o /dev/null -w '%{http_code}' "http://localhost:${HOST_PORT}/healthz/")"
assert_eq "healthz baseline status code" "200" "${healthz_code}"
readyz_body="$(curl_app "http://localhost:${HOST_PORT}/readyz/")"
readyz_code="$(curl_app -o /dev/null -w '%{http_code}' "http://localhost:${HOST_PORT}/readyz/")"
assert_eq "readyz baseline status code" "200" "${readyz_code}"
assert_eq "readyz baseline status field" "ok" "$(echo "${readyz_body}" | json_field status)"
assert_eq "readyz baseline database field" "ok" "$(echo "${readyz_body}" | json_field database)"
assert_eq "readyz baseline cache field" "ok" "$(echo "${readyz_body}" | json_field cache)"

log "--- Redis down: must degrade, not fail ---"
docker stop "${REDIS_NAME}" >/dev/null
sleep 2
readyz_body="$(curl_app "http://localhost:${HOST_PORT}/readyz/")"
readyz_code="$(curl_app -o /dev/null -w '%{http_code}' "http://localhost:${HOST_PORT}/readyz/")"
healthz_code="$(curl_app -o /dev/null -w '%{http_code}' "http://localhost:${HOST_PORT}/healthz/")"
assert_eq "readyz status code with redis down" "200" "${readyz_code}"
assert_eq "readyz status field with redis down" "degraded" "$(echo "${readyz_body}" | json_field status)"
assert_eq "readyz cache field with redis down" "unavailable" "$(echo "${readyz_body}" | json_field cache)"
assert_eq "healthz status code with redis down" "200" "${healthz_code}"
docker start "${REDIS_NAME}" >/dev/null
sleep 2

log "--- Postgres down: must fail readiness, liveness unaffected ---"
docker stop "${PG_NAME}" >/dev/null
sleep 2
readyz_body="$(curl_app "http://localhost:${HOST_PORT}/readyz/")"
readyz_code="$(curl_app -o /dev/null -w '%{http_code}' "http://localhost:${HOST_PORT}/readyz/")"
healthz_code="$(curl_app -o /dev/null -w '%{http_code}' "http://localhost:${HOST_PORT}/healthz/")"
assert_eq "readyz status code with postgres down" "503" "${readyz_code}"
assert_eq "readyz status field with postgres down" "unhealthy" "$(echo "${readyz_body}" | json_field status)"
assert_eq "readyz database field with postgres down" "error" "$(echo "${readyz_body}" | json_field database)"
assert_eq "healthz status code with postgres down" "200" "${healthz_code}"
docker start "${PG_NAME}" >/dev/null
for _ in $(seq 1 30); do
    docker exec "${PG_NAME}" pg_isready -U school_management >/dev/null 2>&1 && break
    sleep 1
done
sleep 2
readyz_code="$(curl_app -o /dev/null -w '%{http_code}' "http://localhost:${HOST_PORT}/readyz/")"
assert_eq "readyz status code after postgres recovers" "200" "${readyz_code}"

log "--- Graceful shutdown on SIGTERM ---"
start_ts="$(date +%s)"
docker stop --time 30 "${APP_NAME}" >/dev/null
end_ts="$(date +%s)"
elapsed=$((end_ts - start_ts))
exit_code="$(docker inspect "${APP_NAME}" --format='{{.State.ExitCode}}')"
docker logs "${APP_NAME}" >>"${EVIDENCE_FILE}" 2>&1 || true
assert_eq "app container exit code after docker stop" "0" "${exit_code}"
if [[ "${elapsed}" -lt 30 ]]; then
    log "PASS: graceful shutdown completed in ${elapsed}s (under the 30s --time bound -- SIGKILL was not needed)"
else
    log "FAIL: shutdown took ${elapsed}s (== the --time bound -- Docker likely escalated to SIGKILL)"
    PASS=0
fi

log "Image kept for evidence: ${IMAGE_TAG}"
if [[ "${PASS}" -eq 1 ]]; then
    log "RESULT: ALL CHECKS PASSED"
    exit 0
else
    log "RESULT: ONE OR MORE CHECKS FAILED -- see above"
    exit 1
fi

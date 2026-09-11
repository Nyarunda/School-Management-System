FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements/base.txt requirements/base.txt
RUN pip install --no-cache-dir -r requirements/base.txt

COPY . .

# Runs as root by default otherwise -- a container escape would then hand an
# attacker root on the host's container runtime, not just app-level access.
# Fixed UID/GID (not just a name) because some platforms (e.g. Kubernetes
# PodSecurityContext) match on numeric id, and a byte-for-byte reproducible
# id is easier to reason about across environments than "whatever useradd
# picked this build."
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --home-dir /app --no-create-home app \
    && chown -R app:app /app
USER app

EXPOSE 8000

# Production-safe default -- Django's own docs say runserver must never be
# used in production (single-threaded, no real concurrency). Local dev uses
# docker-compose.yml, which explicitly overrides this with `runserver` for
# hot-reload convenience; this CMD is what runs when the image is built and
# started directly (e.g. by a cloud platform).
#
# GUNICORN_WORKERS/GUNICORN_THREADS are capacity settings, not performance
# guarantees -- they bound concurrent DB-using execution contexts per
# replica, which interacts directly with the Postgres connection budget (see
# CONN_MAX_AGE's comment in config/settings.py); more workers can make
# things slower once Postgres saturates, not faster. --max-requests/
# --max-requests-jitter periodically recycle workers (jitter avoids every
# worker restarting in sync) as cheap insurance against slow memory growth
# in long-lived processes -- not a substitute for fixing an actual leak.
#
# `exec` matters here: shell-form CMD runs this via `/bin/sh -c`, and
# without exec the shell (not gunicorn) is PID 1 -- `docker stop`'s SIGTERM
# would hit the shell, which does not forward it to gunicorn, so shutdown
# would never be graceful and would always hit the hard SIGKILL timeout
# instead. `exec` replaces the shell process with gunicorn so it receives
# signals directly. Caught by this milestone's Docker-build smoke test.
CMD exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers ${GUNICORN_WORKERS:-4} --threads ${GUNICORN_THREADS:-2} \
    --timeout ${GUNICORN_TIMEOUT:-30} --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-30} \
    --max-requests ${GUNICORN_MAX_REQUESTS:-1000} --max-requests-jitter ${GUNICORN_MAX_REQUESTS_JITTER:-100} \
    --access-logfile - --error-logfile -
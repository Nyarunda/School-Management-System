"""Failure-injection helpers for Milestone 5E, thin wrappers over `docker
compose`. Importable as a library (harness.py schedules these at a precise
offset from its own run clock) and runnable standalone for a manual check.

Always targets the docker-compose.yml + docker-compose.loadtest.yml pair so
a chaos run always hits the load-test stack, never a developer's normal dev
compose.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

# Repo root, not wherever this happens to be invoked from -- chaos.py used
# relative compose-file paths and no explicit `cwd`, which only worked by
# accident when the caller's own cwd happened to be the repo root. harness.py
# is normally run from backend/ (`python -m loadtest.harness`, since
# `loadtest` needs to be importable as a package), which broke every
# scenario here the moment it was actually exercised from that cwd -- found
# live while wiring up RC Area 6 / 5E-3's chaos plan.
REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ["-f", "docker-compose.yml", "-f", "docker-compose.loadtest.yml"]


def _compose(*args):
    subprocess.run(["docker", "compose", *COMPOSE_FILES, *args], check=True, cwd=REPO_ROOT)


def stop_redis():
    print("[chaos] stopping redis")
    _compose("stop", "redis")


def start_redis():
    print("[chaos] starting redis")
    _compose("start", "redis")


def kill_worker():
    print("[chaos] SIGKILL celery-worker")
    _compose("kill", "-s", "SIGKILL", "celery-worker")


def start_worker():
    print("[chaos] starting celery-worker")
    _compose("start", "celery-worker")


def kill_postgres_connections():
    """RC Area 5 Check 4 proved a single targeted pg_terminate_backend()
    mid-transaction produces zero lost/partial state for durable work. This
    is that same proven mechanism, applied to whatever's genuinely in
    flight under real 5E-3 concurrent load -- not a full `docker compose
    stop postgres` outage, which would fail every read/write for its
    duration (Celery beat, health checks, everything) and be a much
    larger-blast-radius scenario than what's actually been validated.
    Postgres itself never stops; killed connections just get
    re-established by the app's own connection pool on their next query,
    the same recovery path Area 5 already proved.
    """
    print("[chaos] terminating all active (non-idle) PostgreSQL backend connections")
    # docker compose exec runs as root by default, which psql can't log in
    # as -- $POSTGRES_USER/$POSTGRES_DB (the postgres image's own env, set
    # from docker-compose.yml) give psql real credentials without hardcoding
    # them here.
    subprocess.run(
        ["docker", "compose", *COMPOSE_FILES, "exec", "-T", "postgres", "sh", "-c",
         'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '
         '"SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
         'WHERE state != \'idle\' AND pid != pg_backend_pid() AND datname = current_database();"'],
        check=True, cwd=REPO_ROOT,
    )


def _no_recovery_needed():
    """Postgres was never stopped -- nothing to restart. Recovery is the
    app's own connection pool reconnecting on its next query, which is the
    behavior under test, not a chaos-script action."""
    print("[chaos] no explicit recovery step -- Postgres stayed up throughout; app reconnects on its own")


SCENARIOS = {
    "redis-outage": (stop_redis, start_redis),
    "kill-worker": (kill_worker, start_worker),
    "postgres-connection-kill": (kill_postgres_connections, _no_recovery_needed),
}


def run_scenario(name, *, at_seconds, duration_seconds):
    """Blocking: sleeps until `at_seconds`, injects the failure, sleeps
    `duration_seconds`, then recovers. Intended to be run in a background
    thread/task by harness.py so the traffic generator keeps running
    throughout, or invoked directly from the CLI for a manual check.
    """
    begin, recover = SCENARIOS[name]
    time.sleep(at_seconds)
    begin()
    time.sleep(duration_seconds)
    recover()


def run_plan(plan):
    """Run several scenarios in one background thread, each at its own
    ABSOLUTE offset (seconds) from this function's own start -- so chaos
    events are individually attributable to a precise real-world window
    (harness.py records real wall-clock timestamps on every metric, so a
    post-hoc analysis can slice "normal" / "degraded" / "post-recovery"
    windows against these exact offsets) rather than drifting relative to
    each other. `plan` is a list of {"scenario", "at_seconds",
    "duration_seconds"} dicts, sorted here by `at_seconds` defensively.
    Sequential, never overlapping -- each scenario fully recovers before
    the next begins, so an observed effect is never a blend of two
    simultaneous failures.
    """
    start = time.monotonic()
    for entry in sorted(plan, key=lambda e: e["at_seconds"]):
        begin, recover = SCENARIOS[entry["scenario"]]
        remaining = entry["at_seconds"] - (time.monotonic() - start)
        if remaining > 0:
            time.sleep(remaining)
        print(f"[chaos] plan: injecting {entry['scenario']} at +{entry['at_seconds']:.0f}s")
        begin()
        time.sleep(entry["duration_seconds"])
        recover()
        print(f"[chaos] plan: {entry['scenario']} recovered at +{time.monotonic()-start:.0f}s")


def main():
    parser = argparse.ArgumentParser(description="Run one or several Milestone 5E chaos scenarios against the load-test compose stack.")
    parser.add_argument("scenario", choices=sorted(SCENARIOS), nargs="?", help="Omit if using --plan-file.")
    parser.add_argument("--at", type=float, default=0, help="Seconds to wait before injecting the failure.")
    parser.add_argument("--duration", type=float, default=60, help="Seconds the failure lasts before recovery.")
    parser.add_argument("--plan-file", default=None,
                         help="Path to a JSON file: a list of {\"scenario\", \"at_seconds\", \"duration_seconds\"} "
                              "objects, run sequentially. Overrides scenario/--at/--duration.")
    args = parser.parse_args()
    if args.plan_file:
        run_plan(json.loads(Path(args.plan_file).read_text()))
    elif args.scenario:
        run_scenario(args.scenario, at_seconds=args.at, duration_seconds=args.duration)
    else:
        parser.error("either a scenario or --plan-file is required")


if __name__ == "__main__":
    main()

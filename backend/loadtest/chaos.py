"""Failure-injection helpers for Milestone 5E, thin wrappers over `docker
compose`. Importable as a library (harness.py schedules these at a precise
offset from its own run clock) and runnable standalone for a manual check.

Always targets the docker-compose.yml + docker-compose.loadtest.yml pair so
a chaos run always hits the load-test stack, never a developer's normal dev
compose.
"""
import argparse
import subprocess
import time

COMPOSE_FILES = ["-f", "docker-compose.yml", "-f", "docker-compose.loadtest.yml"]


def _compose(*args):
    subprocess.run(["docker", "compose", *COMPOSE_FILES, *args], check=True)


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


SCENARIOS = {
    "redis-outage": (stop_redis, start_redis),
    "kill-worker": (kill_worker, start_worker),
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


def main():
    parser = argparse.ArgumentParser(description="Run one Milestone 5E chaos scenario against the load-test compose stack.")
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--at", type=float, default=0, help="Seconds to wait before injecting the failure.")
    parser.add_argument("--duration", type=float, default=60, help="Seconds the failure lasts before recovery.")
    args = parser.parse_args()
    run_scenario(args.scenario, at_seconds=args.at, duration_seconds=args.duration)


if __name__ == "__main__":
    main()

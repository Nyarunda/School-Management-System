"""Background metrics sampler for a Milestone 5E run: polls PostgreSQL
connection/lock/deadlock stats and durable-work backlog depth/age every
few seconds for the run's duration, independent of the request-driving
harness. The only piece of the load-test tooling that needs django.setup()
-- everything it reads (pg_stat_*, NotificationOutbox, MpesaCallbackLog)
needs the ORM/DB connection.

Run alongside harness.py, pointed at the same run_id, for the same
duration: `python -m loadtest.sampler --run-id <id> --duration 1800`.
"""
import argparse
import json
import os
import time
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.finance.models import MpesaCallbackLog, MpesaCallbackStatus  # noqa: E402
from apps.notifications.models import NotificationOutbox  # noqa: E402
from apps.activity.durable_work import DurableWorkStatus  # noqa: E402

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def sample_postgres():
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*), count(*) FILTER (WHERE state = 'active') FROM pg_stat_activity")
        total_connections, active_connections = cursor.fetchone()
        cursor.execute("SELECT deadlocks, conflicts FROM pg_stat_database WHERE datname = current_database()")
        row = cursor.fetchone()
        deadlocks, conflicts = row if row else (0, 0)
        cursor.execute("SELECT count(*) FROM pg_locks WHERE NOT granted")
        (lock_waits,) = cursor.fetchone()
    return {
        "total_connections": total_connections, "active_connections": active_connections,
        "deadlocks": deadlocks, "conflicts": conflicts, "lock_waits": lock_waits,
    }


def sample_backlogs():
    notification_pending = NotificationOutbox.objects.filter(status__in=[DurableWorkStatus.PENDING, DurableWorkStatus.PROCESSING])
    oldest_notification = notification_pending.order_by("available_at").values_list("available_at", flat=True).first()
    received_callbacks = MpesaCallbackLog.objects.filter(status=MpesaCallbackStatus.RECEIVED)
    oldest_callback = received_callbacks.order_by("created_at").values_list("created_at", flat=True).first()
    now = timezone.now()
    return {
        "notification_backlog_count": notification_pending.count(),
        "notification_backlog_age_seconds": (now - oldest_notification).total_seconds() if oldest_notification else 0,
        "callback_backlog_count": received_callbacks.count(),
        "callback_backlog_age_seconds": (now - oldest_callback).total_seconds() if oldest_callback else 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Milestone 5E server-side metrics sampler.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--duration", type=int, default=1800, help="Total seconds to sample for.")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()

    samples = []
    end_time = time.monotonic() + args.duration
    while time.monotonic() < end_time:
        sample = {"ts": time.time()}
        try:
            sample.update(sample_postgres())
        except Exception as error:  # pg_stat_* isn't available on SQLite dev runs
            sample["postgres_error"] = str(error)
        sample.update(sample_backlogs())
        samples.append(sample)
        time.sleep(args.interval)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"{args.run_id}-samples.json").write_text(json.dumps(samples, indent=2))
    print(f"[sampler] wrote {len(samples)} samples for run {args.run_id}")


if __name__ == "__main__":
    main()

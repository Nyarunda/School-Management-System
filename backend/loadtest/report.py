"""Combines a Milestone 5E run's client-side metrics (harness.py), server-
side samples (sampler.py), and reconciliation verdict (loadtest_reconcile
management command) into one Markdown report -- the actual deliverable a
human reads to answer "where's the ceiling and did anything break."
"""
import argparse
import json
from pathlib import Path
from statistics import median

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def _percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * pct / 100))
    return ordered[index]


def _load(path):
    return json.loads(path.read_text()) if path.exists() else None


def summarize_metrics(metrics):
    by_key = {}
    for row in metrics:
        key = (row["phase"], row["traffic_class"])
        by_key.setdefault(key, {"latencies": [], "errors": 0, "total": 0})
        bucket = by_key[key]
        bucket["latencies"].append(row["latency_ms"])
        bucket["total"] += 1
        if row["status_code"] is None or row["status_code"] >= 400:
            bucket["errors"] += 1
    rows = []
    for (phase, traffic_class), bucket in sorted(by_key.items()):
        latencies = bucket["latencies"]
        rows.append({
            "phase": phase, "traffic_class": traffic_class, "count": bucket["total"],
            "error_rate": bucket["errors"] / bucket["total"] if bucket["total"] else 0,
            "p50_ms": round(median(latencies), 1) if latencies else None,
            "p95_ms": round(_percentile(latencies, 95), 1) if latencies else None,
            "p99_ms": round(_percentile(latencies, 99), 1) if latencies else None,
        })
    return rows


def render(run_id, metrics, samples, verdict):
    lines = [f"# Load-test report: {run_id}", ""]

    lines.append("## Request metrics by phase and traffic class")
    lines.append("")
    lines.append("| Phase | Traffic class | Count | Error rate | p50 (ms) | p95 (ms) | p99 (ms) |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in summarize_metrics(metrics or []):
        lines.append(
            f"| {row['phase']} | {row['traffic_class']} | {row['count']} | {row['error_rate']:.2%} | "
            f"{row['p50_ms']} | {row['p95_ms']} | {row['p99_ms']} |"
        )
    lines.append("")

    if samples:
        lines.append("## Server-side samples over the run")
        lines.append("")
        lines.append("| ts | DB connections (active/total) | Deadlocks | Lock waits | Notification backlog (count/age s) | Callback backlog (count/age s) |")
        lines.append("|---|---|---|---|---|---|")
        for sample in samples:
            lines.append(
                f"| {sample['ts']:.0f} | {sample.get('active_connections', '?')}/{sample.get('total_connections', '?')} | "
                f"{sample.get('deadlocks', '?')} | {sample.get('lock_waits', '?')} | "
                f"{sample.get('notification_backlog_count', '?')}/{sample.get('notification_backlog_age_seconds', 0):.0f} | "
                f"{sample.get('callback_backlog_count', '?')}/{sample.get('callback_backlog_age_seconds', 0):.0f} |"
            )
        lines.append("")

    lines.append("## Reconciliation verdict")
    lines.append("")
    if verdict is None:
        lines.append("Not yet run -- see `python manage.py loadtest_reconcile --run-id " + run_id + "`.")
    elif not verdict.get("violations"):
        lines.append("**PASS** -- no violations across every check.")
    else:
        lines.append(f"**FAIL** -- {len(verdict['violations'])} violation(s):")
        lines.append("")
        for violation in verdict["violations"]:
            lines.append(f"- `{violation['check']}`: {violation['detail']}")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Render a Milestone 5E Markdown report for one run.")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    metrics = _load(REPORTS_DIR / f"{args.run_id}-metrics.json")
    samples = _load(REPORTS_DIR / f"{args.run_id}-samples.json")
    verdict = _load(REPORTS_DIR / f"{args.run_id}-verdict.json")

    report = render(args.run_id, metrics, samples, verdict)
    out_path = REPORTS_DIR / f"{args.run_id}.md"
    out_path.write_text(report)
    print(f"[report] wrote {out_path}")


if __name__ == "__main__":
    main()

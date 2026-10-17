"""Read a load test's results and say whether it passed (campaign session 4, §B.2).

    python perf/load_report.py --csv /tmp/servabo-load --api-log /tmp/servabo-e2e-logs/api.log

Reads Locust's `<prefix>_stats.csv` and `<prefix>_failures.csv` and the API's log, prints a
Markdown table of every request type (count, median, p95, p99, failures), and fails (exit 1) when:

* any request answered 5xx;
* `/api/v1/health` had a p95 over HEALTH_P95_MS (the event loop was blocked);
* the API log shows the database connection pool ran out ("QueuePool limit") or a traceback.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

HEALTH_P95_MS = 250


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", required=True, help="the --csv prefix given to locust")
    parser.add_argument("--api-log", help="the API's log file for the run")
    parser.add_argument("--since", help="only API log lines at or after this timestamp prefix, e.g. '2026-10-13 21:00'")
    args = parser.parse_args(argv)

    stats = list(csv.DictReader(open(f"{args.csv}_stats.csv")))
    failures = list(csv.DictReader(open(f"{args.csv}_failures.csv"))) if Path(f"{args.csv}_failures.csv").exists() else []
    problems: list[str] = []

    print("| Request | Count | Median ms | p95 ms | p99 ms | Failures |")
    print("|---|---:|---:|---:|---:|---:|")
    for row in stats:
        name = row["Name"] if row["Type"] else "**All requests**"
        if row["Name"] == "Aggregated":
            name = "**All requests**"
        print(f"| {name} | {row['Request Count']} | {row['Median Response Time']} | {row['95%']} | {row['99%']} | "
              f"{row['Failure Count']} |")
        if row["Name"] == "health" and float(row["95%"] or 0) > HEALTH_P95_MS:
            problems.append(f"/health p95 {row['95%']} ms is over {HEALTH_P95_MS} ms: the event loop was blocked")

    for row in failures:
        if re.search(r"\b5\d\d\b", row.get("Error", "")):
            problems.append(f"5xx: {row['Name']}: {row['Error'][:160]} ({row['Occurrences']}x)")

    if args.api_log:
        lines = Path(args.api_log).read_text(errors="replace").splitlines()
        if args.since:
            lines = [line for line in lines if line[1:17] >= args.since[:16]]
        fives = [line for line in lines if re.search(r'" 5\d\d\b', line)]
        if fives:
            problems.append(f"{len(fives)} 5xx answers in the API log, first: {fives[0][:200]}")
        if any("QueuePool limit" in line for line in lines):
            problems.append("the database connection pool ran out (QueuePool limit)")
        tracebacks = sum("Traceback" in line for line in lines)
        if tracebacks:
            problems.append(f"{tracebacks} tracebacks in the API log")

    print()
    if problems:
        print("FAILED:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("PASSED: no 5xx, /health stayed fast, the pool never ran out, no tracebacks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

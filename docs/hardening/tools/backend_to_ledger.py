#!/usr/bin/env python3
"""Promote COVERAGE.csv rows that a PASSING backend test drove (Phase 5).

    python docs/hardening/tools/backend_to_ledger.py <hits.json from route_recorder.py>

An `untested` endpoint row becomes `smoke` when at least one passing test got
a response from that exact route (method and template), excluding 405 (wrong
method) and 5xx. An `untested` background_job row becomes `smoke` when a
passing test ran its handler through the job registry. `deep` is never given
here: a recorded call does not show that the test checked the result. Rows
are never downgraded and rows already `smoke`/`deep` keep their evidence.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "COVERAGE.csv"
DATE = "2026-10-06"


def main(path: str) -> int:
    hits = json.loads(Path(path).read_text(encoding="utf-8"))
    passed = set(hits["passed"])
    evidence: dict[tuple[str, str], dict[str, set]] = defaultdict(lambda: {"tests": set(), "statuses": set()})
    for test, method, route, status in hits["routes"]:
        if test in passed and status != 405 and status < 500:
            entry = evidence[("endpoint", f"{method} {route}")]
            entry["tests"].add(test)
            entry["statuses"].add(status)
    for test, job_type in hits["jobs"]:
        if test in passed:
            evidence[("background_job", job_type)]["tests"].add(test)

    with LEDGER.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        rows = list(reader)

    promoted = defaultdict(int)
    already = 0
    index = {(row["type"], row["id"]): row for row in rows}
    for key, entry in evidence.items():
        row = index.get(key)
        if row is None:
            continue
        if row["status"] not in ("", "untested"):
            already += 1
            continue
        files = sorted({"backend/" + test.split("::", 1)[0] for test in entry["tests"]})
        statuses = ",".join(str(s) for s in sorted(entry["statuses"])) or "ran"
        row["status"] = "smoke"
        row["test_file"] = ";".join([t for t in (row.get("test_file") or "").split(";") if t] + files[:3])
        row["last_result"] = (f"pass {DATE} (backend suite, Phase 5): {len(entry['tests'])} passing test(s) "
                              f"drove it; responses {statuses}")
        promoted[key[0]] += 1

    with LEDGER.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    unmatched = sorted(k[1] for k in evidence if k not in index)
    print(f"promoted untested -> smoke: {dict(promoted)}; already covered: {already}; "
          f"recorded but not in the ledger: {len(unmatched)}")
    for name in unmatched[:15]:
        print("  not in ledger:", name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

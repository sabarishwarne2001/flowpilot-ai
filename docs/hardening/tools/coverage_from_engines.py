"""Mark COVERAGE.csv rows exercised by the live engine tests (Phase 4).

Usage (from backend/):
    ENGINE_ROUTE_LOG=/tmp/engine_routes.tsv pytest -q tests/engines
    python ../docs/hardening/tools/coverage_from_engines.py /tmp/engine_routes.tsv

The engine harness (tests/engines/conftest.py) appends one line per API call
("HTTP\\t<METHOD> <path> <status>") and per drained job ("JOB\\t<job_type>").
Each concrete path is matched against the live route table, and the matching
`endpoint` / `background_job` rows become `deep`. Nothing is ever downgraded.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "COVERAGE.csv"
TEST_REF = "backend/tests/engines"
RESULT = "pass (engines, Phase 4)"


def main(log_path: str) -> None:
    sys.path.insert(0, str(Path.cwd()))
    from fastapi.routing import APIRoute
    from starlette.routing import Match

    from app.main import app

    routes = [r for r in app.routes if isinstance(r, APIRoute)]
    endpoints: Counter[str] = Counter()
    jobs: set[str] = set()
    unmatched: set[str] = set()
    for line in Path(log_path).read_text(encoding="utf-8").splitlines():
        kind, _, value = line.partition("\t")
        if kind == "JOB":
            jobs.add(value.strip())
            continue
        method, path, _status = value.split(" ")
        scope = {"type": "http", "path": path, "method": method, "root_path": ""}
        for route in routes:
            match, _ = route.matches(scope)
            if match is Match.FULL:
                endpoints[f"{method} {route.path}"] += 1
                break
        else:
            unmatched.add(f"{method} {path}")

    rows = list(csv.reader(LEDGER.open(encoding="utf-8", newline="")))
    header, body = rows[0], rows[1:]
    col = {name: i for i, name in enumerate(header)}
    marked = 0
    for row in body:
        kind, ident = row[col["type"]], row[col["id"]]
        hit = (kind == "endpoint" and ident in endpoints) or (kind == "background_job" and ident in jobs)
        if not hit or row[col["status"]] == "deep" and RESULT in row[col["last_result"]]:
            continue
        files = [f for f in row[col["test_file"]].split(";") if f]
        if TEST_REF not in files:
            files.append(TEST_REF)
        row[col["test_file"]] = ";".join(files)
        row[col["status"]] = "deep"
        row[col["last_result"]] = RESULT
        marked += 1
    with LEDGER.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows([header, *body])
    print(f"endpoints exercised: {len(endpoints)}; jobs: {len(jobs)}; rows marked deep: {marked}")
    if unmatched:
        print(f"unmatched paths (not in the route table): {sorted(unmatched)[:10]}")


if __name__ == "__main__":
    main(sys.argv[1])

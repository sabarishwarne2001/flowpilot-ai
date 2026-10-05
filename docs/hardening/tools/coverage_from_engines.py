"""Mark COVERAGE.csv rows exercised by the live engine tests (Phase 4).

Usage (from backend/):
    ENGINE_ROUTE_LOG=/tmp/engine_routes.tsv pytest -q tests/engines
    python ../docs/hardening/tools/coverage_from_engines.py /tmp/engine_routes.tsv

The engine harness (tests/engines/conftest.py) appends one line per API call
("HTTP\\t<METHOD> <path> <status>") and per drained job ("JOB\\t<job_type>").
Each concrete path is matched against the live route table. A route that
answered at least one call successfully (2xx/3xx), and every drained job
type, becomes `deep`; a route only ever answered with a refusal (4xx - a
role, plan or tenant check) becomes `smoke` with "refusal proven", since its
gate was proven but not its function. Nothing is ever downgraded.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "COVERAGE.csv"
TEST_REF = "backend/tests/engines"
RESULT = "pass (engines, Phase 4)"
REFUSAL = "refusal proven (engines, Phase 4)"
RANK = {"untested": 0, "": 0, "smoke": 1, "deep": 2}


def main(log_path: str) -> None:
    sys.path.insert(0, str(Path.cwd()))
    from fastapi.routing import APIRoute
    from starlette.routing import Match

    from app.main import app

    routes = [r for r in app.routes if isinstance(r, APIRoute)]
    endpoints: Counter[str] = Counter()
    succeeded: set[str] = set()
    jobs: set[str] = set()
    unmatched: set[str] = set()
    for line in Path(log_path).read_text(encoding="utf-8").splitlines():
        kind, _, value = line.partition("\t")
        if kind == "JOB":
            jobs.add(value.strip())
            continue
        method, path, status = value.split(" ")
        scope = {"type": "http", "path": path, "method": method, "root_path": ""}
        for route in routes:
            match, _ = route.matches(scope)
            if match is Match.FULL:
                endpoints[f"{method} {route.path}"] += 1
                if int(status) < 400:
                    succeeded.add(f"{method} {route.path}")
                break
        else:
            unmatched.add(f"{method} {path}")

    rows = list(csv.reader(LEDGER.open(encoding="utf-8", newline="")))
    header, body = rows[0], rows[1:]
    col = {name: i for i, name in enumerate(header)}
    marked = Counter()
    for row in body:
        kind, ident = row[col["type"]], row[col["id"]]
        if kind == "background_job" and ident in jobs or kind == "endpoint" and ident in succeeded:
            status, result = "deep", RESULT
        elif kind == "endpoint" and ident in endpoints:
            status, result = "smoke", REFUSAL
        else:
            continue
        if RANK.get(row[col["status"]], 0) > RANK[status]:
            continue
        files = [f for f in row[col["test_file"]].split(";") if f]
        if TEST_REF not in files:
            files.append(TEST_REF)
        row[col["test_file"]] = ";".join(files)
        if RANK.get(row[col["status"]], 0) < RANK[status] or not row[col["last_result"]]:
            row[col["last_result"]] = result
        row[col["status"]] = status
        marked[status] += 1
    with LEDGER.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows([header, *body])
    print(f"endpoints exercised: {len(endpoints)} ({len(succeeded)} answered successfully); "
          f"jobs: {len(jobs)}; rows marked: {dict(marked)}")
    if unmatched:
        print(f"unmatched paths (not in the route table): {sorted(unmatched)[:10]}")


if __name__ == "__main__":
    main(sys.argv[1])

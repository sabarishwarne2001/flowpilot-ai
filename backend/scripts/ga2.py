#!/usr/bin/env python3
"""ARCH50-S1:ga2 — GA-2: the whole verification chain on a CLEAN database, the same way on Linux and Windows.

    python scripts/ga2.py --database flowpilot_ga2 --create   # drop + create + migrate + seed, then run everything
    python scripts/ga2.py --database flowpilot_ga2            # run against a database this script created earlier
    python scripts/ga2.py --database flowpilot_ga2 --only verify_arch47,verify_hardening_master
    python scripts/ga2.py --list                              # what runs, and every recorded known failure

(`run_arch50.ps1 -GA2` runs the first line on Windows.)

WHAT "CLEAN" MEANS
==================
`--create` drops and re-creates the named database on the server the application is configured for
(POSTGRES_HOST / PORT / USER / PASSWORD), migrates it from an EMPTY schema through every revision to
`arch50_step1_sovereign_revops` -- never `head`: the lossy contract step stays held -- publishes the seat price book
and the four tiers (unpriced: a clean database has no gateway price ids), and nothing else. Every verifier then runs
with POSTGRES_DB and DATABASE_URL pointed at it, one after another (never two database suites at once).

THE DATABASE IS THROWAWAY. The two ARCH-10 verifiers that must never run against a database you back up
(`scripts/verify_arch10_step3.py`, `scripts/verify_arch10_step9.py`) run here, and only when the database name
contains "ga2" or "scratch": this script refuses to run them anywhere else.

KNOWN FAILURES are listed below with the reason each is not a defect of this release (or not fixable in a sandbox).
A known failure that PASSES is reported as such; any other failure is a REGRESSION and the run exits 1.

Evidence: backend/evidence/ga2/ -- one log per verifier, ga2.json, summary.txt, and a copy of every evidence JSON a
verifier rewrote (restore the committed ones with `git checkout -- backend/evidence` before generating an apply).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
OUT = BACKEND / "evidence" / "ga2"
RELEASE_HEAD = "arch50_step1_sovereign_revops"
DESTRUCTIVE = ("verify_arch10_step3", "verify_arch10_step9")

#: (name, command relative to backend/, timeout in seconds). Order: the chain 41-50, the hardening harnesses, then
#: every earlier verifier the milestone certifications list.
SUITE: tuple[tuple[str, tuple[str, ...], int], ...] = (
    *((f"verify_arch{n}", (f"verify_arch{n}.py", "--db"), 3600) for n in range(41, 51)),
    ("verify_hardening_master", ("verify_hardening_master.py", "--db"), 3600),
    ("verify_hardening_final", ("verify_hardening_final.py", "--db"), 3600),
    ("verify_hardening_tier1", ("verify_hardening_tier1.py", "--db"), 3600),
    ("verify_hardening_tier2", ("verify_hardening_tier2.py", "--db"), 3600),
    ("verify_hardening_tier3", ("verify_hardening_tier3.py", "--db"), 3600),
    ("verify_arch40", ("verify_arch40.py", "--db"), 3600),
    ("verify_arch39", ("verify_arch39.py", "--db"), 3600),
    ("verify_arch38", ("verify_arch38.py", "--db"), 3600),
    ("verify_arch37", ("verify_arch37.py", "--db"), 3600),
    ("verify_arch36", ("verify_arch36.py", "--db"), 3600),
    ("verify_arch35", ("verify_arch35.py", "--db"), 3600),
    ("verify_arch34", ("verify_arch34.py", "--db"), 3600),
    ("verify_arch31", ("verify_arch31.py", "--db"), 3600),
    ("verify_arch31_step0", ("verify_arch31_step0.py", "--db"), 3600),
    ("verify_arch16", ("scripts/verify_arch16.py",), 1800),
    ("verify_arch30_tranche1", ("scripts/verify_arch30_tranche1.py",), 1800),
    ("verify_arch30_tranche3", ("scripts/verify_arch30_tranche3.py",), 1800),
    ("verify_arch12", ("scripts/verify_arch12.py",), 1800),
    ("verify_arch25", ("scripts/verify_arch25.py",), 1800),
    ("verify_arch21", ("scripts/verify_arch21.py",), 1800),
    ("verify_arch09_step4_5", ("scripts/verify_arch09_step4_5.py",), 1800),
    ("test_ssrf_client", ("-m", "pytest", "-q", "tests/test_ssrf_client.py"), 1800),
    ("test_arch12_budget_and_isolation", ("-m", "pytest", "-q", "tests/services/test_arch12_budget_and_isolation.py"), 1800),
    ("automation_conformance", ("scripts/automation_conformance.py", "--json", "{out}/automation_conformance.json"), 1800),
    ("verify_arch10_step3", ("scripts/verify_arch10_step3.py",), 1800),
    ("verify_arch10_step9", ("scripts/verify_arch10_step9.py",), 1800),
)

#: name -> (the gates expected to fail, why). Reproduced on the ARCH-49 baseline (b76347a) unless stated otherwise;
#: the GA-2 report records each with its evidence.
KNOWN: dict[str, tuple[str, str]] = {
    "verify_arch38": ("A1", "apply_arch38.py refuses app/services/ingestion/preset_service.py, which ARCH-42 edited "
                            "after ARCH-38 shipped; a historical apply script, not a product defect"),
    "verify_arch30_tranche1": ("30T1-G4", "no KNOWN_METERS entry for bundled capabilities (a capability is sold "
                                          "inside a plan, it is not a meter); the gate predates bundled capabilities"),
    "verify_arch21": ("G8", "ARCH-21 reserved /api/v1/public for the API-key gateway; ARCH-43's recipient upload "
                            "link and ARCH-46's calendar feed are token routes under that prefix by design (their own "
                            "token check and POLICY_PUBLIC_READ; no API key exists to tier them). Moving them would "
                            "break every link already issued. (G16, the 429 without rate limit headers, is FIXED by "
                            "ARCH-50.)"),
    "verify_hardening_tier3": ("ARCH-38 idempotency, app/services/ingestion/preset_service.py",
                               "the same apply_arch38.py refusal as verify_arch38 A1 (preset_service.py was edited by "
                               "ARCH-42 after ARCH-38 shipped); a historical apply script, not a product defect"),
    "verify_hardening_master": ("D1, D5, D6, D7", "live payment-gateway gates: real gateway price ids, webhook "
                                                  "secrets and a reachable Stripe / Dodo account (a clean database "
                                                  "is seeded --allow-unpriced: D1 finds Enterprise unpriced, D7 "
                                                  "cannot sync seats to a price that does not exist)"),
    "verify_hardening_final": ("Payment loop (Stripe)", "a real Stripe account and webhook secret"),
    "verify_arch10_step9": ("G5.3, ARCH-10 is not closed", "G5.3 runs Alembic autogenerate, which demands a database "
                                                          "AT the script head; the head is the held, lossy contract "
                                                          "step arch40_step3 (never run unasked), so GA-2's database "
                                                          "stops at the release head by design. Column drift is gated "
                                                          "per milestone instead (verify_arch50 D2 and its "
                                                          "predecessors). G2.2, which failed since 2026-09-06, is FIXED "
                                                          "by ARCH-50 (the database's pipeline stage guard)"),
}


def _database_url(database: str) -> str:
    from app.core.config import settings

    return (f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:"
            f"{settings.POSTGRES_PORT}/{database}")


def _env(database: str) -> dict[str, str]:
    return {**os.environ, "POSTGRES_DB": database, "DATABASE_URL": _database_url(database), "PYTHONUTF8": "1"}


def _run(cmd: list[str], env: dict, log: Path, timeout: int) -> tuple[int, float, str]:
    started = time.monotonic()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8", errors="replace") as handle:
        try:
            proc = subprocess.run(cmd, cwd=str(BACKEND), env=env, stdout=handle, stderr=subprocess.STDOUT,
                                  timeout=timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            handle.write(f"\nTIMEOUT after {timeout}s\n")
            code = 124
    text = log.read_text(encoding="utf-8", errors="replace")
    return code, round(time.monotonic() - started, 1), text


def create(database: str) -> dict:
    """Drop, create, migrate from an empty schema, seed. Returns what it did."""
    import psycopg2
    from psycopg2 import sql

    from app.core.config import settings

    if not re.fullmatch(r"[a-z_][a-z0-9_]{2,62}", database):
        raise SystemExit(f"{database!r} is not a plain database name")
    if database == settings.POSTGRES_DB:
        raise SystemExit(f"{database} is the application's configured database; GA-2 needs its own")
    admin = psycopg2.connect(host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, user=settings.POSTGRES_USER,
                             password=settings.POSTGRES_PASSWORD, dbname="postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
        cur.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(sql.Identifier(database)))
    admin.close()
    env = _env(database)
    report: dict = {"database": database}
    for name, cmd in (("migrate", [sys.executable, "-m", "alembic", "upgrade", RELEASE_HEAD]),
                      ("seed_price_book", [sys.executable, "scripts/seed_price_book.py"]),
                      ("seed_quota_tiers", [sys.executable, "scripts/seed_quota_tiers.py", "--allow-unpriced"])):
        code, seconds, text = _run(cmd, env, OUT / "logs" / f"setup_{name}.log", 3600)
        report[name] = {"exit": code, "seconds": seconds}
        if code != 0:
            raise SystemExit(f"GA-2 setup step {name} failed (exit {code}); see {OUT / 'logs' / f'setup_{name}.log'}\n"
                             + text[-2000:])
    probe = psycopg2.connect(_database_url(database))
    with probe.cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        report["alembic"] = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        report["tables"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM organizations")
        report["organizations"] = cur.fetchone()[0]
    probe.close()
    assert report["alembic"] == [RELEASE_HEAD], report
    return report


def _counts(text: str) -> Optional[dict]:
    tail = text[-6000:]
    m = list(re.finditer(r"(\d+) passed, (\d+) failed", tail))
    if m:
        return {"passed": int(m[-1].group(1)), "failed": int(m[-1].group(2))}
    m = list(re.finditer(r"(\d+) passed(?: in|,)", tail))
    if m:
        failed = re.search(r"(\d+) failed", tail)
        return {"passed": int(m[-1].group(1)), "failed": int(failed.group(1)) if failed else 0}
    return None


def _failed_gates(text: str) -> list[str]:
    # "[FAIL] name", "[ FAIL ] name", "FAIL  [db] name", pytest's "FAILED path::test" -- not a summary line such as
    # "FAILED — 2 gate(s) failed" or "FAILED: 2 of 22 checks failed."
    names = re.findall(r"^\s*(?:\[\s*FAIL\s*\]|FAIL\b|FAILED(?=\s+\S+::)|✗)\s*(?:\[[^\]]*\]\s*)?([^\n]{0,100})",
                       text, re.M)
    return [n.strip() for n in names][:30]


def _unexplained(failed: list[str], gates: str) -> list[str]:
    """The failed gates a KNOWN entry does not name. A known failure passes triage only when every failed gate is
    one it names -- a verifier that is known to fail on A1 and now also fails on D3 is a regression."""
    ids = [g.strip() for g in gates.split(",") if g.strip()]
    return [f for f in failed if not any(re.match(re.escape(i) + r"(?=$|[\s:.,(—-])", f) for i in ids)]


def _evidence_snapshot() -> dict[Path, float]:
    base = BACKEND / "evidence"
    return {p: p.stat().st_mtime for p in base.rglob("*.json") if OUT not in p.parents} if base.exists() else {}


def run(database: str, only: tuple[str, ...]) -> int:
    env = _env(database)
    results = []
    started = datetime.now(timezone.utc)
    for name, args, timeout in SUITE:
        if only and name not in only:
            continue
        if name in DESTRUCTIVE and not re.search(r"ga2|scratch", database):
            results.append({"name": name, "status": "NOT RUN", "reason": "never against a database you back up"})
            continue
        cmd = [sys.executable, *(a.replace("{out}", str(OUT)) for a in args)]
        before = _evidence_snapshot()
        print(f"--- {name}: {' '.join(args)}", flush=True)
        code, seconds, text = _run(cmd, env, OUT / "logs" / f"{name}.log", timeout)
        for path, mtime in _evidence_snapshot().items():
            if before.get(path) != mtime:
                target = OUT / "json" / path.relative_to(BACKEND / "evidence")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        counts = _counts(text)
        known = KNOWN.get(name)
        failed = _failed_gates(text) if code != 0 else []
        if code == 0:
            status = "PASS" if not known else "PASS (a known failure did not reproduce)"
        elif known and failed and not _unexplained(failed, known[0]):
            status = "KNOWN"
        elif known:
            status = "FAIL (beyond the known failure)" if failed else "FAIL (the failed gates could not be read)"
        else:
            status = "FAIL"
        entry = {"name": name, "command": " ".join(args), "exit": code, "seconds": seconds, "counts": counts,
                 "status": status}
        if code != 0:
            entry["failed"] = failed
            if known:
                entry["unexplained"] = _unexplained(failed, known[0])
            entry["tail"] = text[-1500:]
        if known:
            entry["known"] = {"gates": known[0], "reason": known[1]}
        results.append(entry)
        print(f"    {status}  exit {code}  {counts or ''}  {seconds}s", flush=True)
    summary = {
        "milestone": "ARCH-50 GA-2", "database": database, "release_head": RELEASE_HEAD,
        "started": started.isoformat(), "finished": datetime.now(timezone.utc).isoformat(),
        "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "results": results,
        "totals": {k: sum(1 for r in results if r["status"].startswith(k)) for k in ("PASS", "KNOWN", "FAIL", "NOT RUN")},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ga2.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    lines = [f"GA-2 on {database} ({summary['started']} .. {summary['finished']})", ""]
    for r in results:
        counts = r.get("counts") or {}
        extra = f"{counts.get('passed')}/{counts.get('passed', 0) + counts.get('failed', 0)}" if counts else ""
        lines.append(f"{r['status']:<44} {r['name']:<34} {extra:>9}  {r.get('seconds', '')}s"
                     + (f"  [{r['known']['gates']}]" if r.get("known") and r["status"] == "KNOWN" else ""))
    lines += ["", json.dumps(summary["totals"])]
    (OUT / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if summary["totals"]["FAIL"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", default="flowpilot_ga2")
    parser.add_argument("--create", action="store_true", help="drop, create, migrate and seed the database first")
    parser.add_argument("--only", default="", help="comma-separated suite names")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for name, cmd, _ in SUITE:
            print(f"  {name:<34} {' '.join(cmd)}" + (f"   KNOWN: {KNOWN[name][0]}" if name in KNOWN else ""))
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    if args.create:
        report = create(args.database)
        (OUT / "setup.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"created {args.database}: {report}")
    return run(args.database, tuple(x.strip() for x in args.only.split(",") if x.strip()))


if __name__ == "__main__":
    sys.exit(main())

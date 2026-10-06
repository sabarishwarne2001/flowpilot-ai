"""HARDENING TIER 3 — verification harness.

Run from backend/ after tiers 1 and 2:

    python verify_hardening_tier3.py                 # offline gates
    python verify_hardening_tier3.py --db            # + real-auth HTTP gates (Redis + PostgreSQL)
    python verify_hardening_tier3.py --mutation      # + each gate must catch its defect
    python verify_hardening_tier3.py --frontend      # + tsc, eslint, vite build
    python verify_hardening_tier3.py --previous      # + re-run tier 1 and tier 2 harnesses (same flags)
    python verify_hardening_tier3.py --all
"""

from __future__ import annotations

import argparse
import ast
import os
import pathlib
import subprocess
import sys
import uuid
from typing import Any, Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
FRONTEND = HERE.parent / "frontend"
SRC = FRONTEND / "src"
sys.path.insert(0, str(HERE))
os.chdir(HERE)
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not ok else ""))
    return ok


def run_gate(name: str, fn: Callable[[], Optional[str]]) -> bool:
    try:
        problem = fn()
    except Exception as exc:  # noqa: BLE001
        problem = f"{type(exc).__name__}: {exc}"
    return record(name, problem is None, problem or "")


def _read(rel: str) -> str:
    return (HERE / rel).read_text(encoding="utf-8-sig")


def g_d13_all_paths() -> Optional[str]:
    """D13: all three upload paths narrow by the workspace setting (ARCH40-S2)."""
    for rel in ("app/api/v1/work_items.py", "app/api/v1/ingestion.py", "app/workers/handlers/ingestion.py"):
        calls = [n for n in ast.walk(ast.parse(_read(rel))) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "validate_spooled"]
        if not calls:
            return f"{rel}: no validate_spooled call"
        for call in calls:
            value = {k.arg: k.value for k in call.keywords}.get("allowed_mimes")
            if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "workspace_allowed_mimes"):
                return f"{rel}:{call.lineno} passes the platform list unfiltered"
    for rel in ("app/api/v1/ingestion.py", "app/workers/handlers/ingestion.py"):
        if "ARCH40-S2:" not in _read(rel):
            return f"{rel} lacks the ARCH40-S2 superseding sentinel"
    return None


def g_arch38_superseded() -> Optional[str]:
    """ARCH-38's idempotency check stays green with the ARCH40-S2 edits."""
    out = subprocess.run([sys.executable, "apply_arch38.py", "--check"], cwd=HERE, capture_output=True, text=True, timeout=300)
    if out.returncode != 0 or "0 file(s) would change" not in out.stdout:
        return (out.stdout + out.stderr)[-300:]
    return None


def g_d36_enum_serialisation() -> Optional[str]:
    """D36: no API response serialises an enum with str() (\"DomainStatus.PENDING\")."""
    import re

    pat = re.compile(r"\bstr\(([A-Za-z_][A-Za-z_0-9]*)\.(status|role|protocol|kind|state)\)")
    hits = [f"{p}:{i}" for p in pathlib.Path("app/api").rglob("*.py")
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if pat.search(line)]
    return f"str(enum) serialisations: {hits[:5]}" if hits else None


def g_frontend_t3() -> Optional[str]:
    problems = []
    rq = (SRC / "pages/Assertions/AssertionReviewQueue.tsx").read_text(encoding="utf-8")
    if "<ClauseChecksPanel" not in rq:
        problems.append("D21 clause-check panel not mounted")
    panel = (SRC / "components/assertions/ClauseChecksPanel.tsx").read_text(encoding="utf-8")
    if "<ClauseAssertionBlock" not in panel:
        problems.append("D21 ClauseAssertionBlock not mounted")
    login = (SRC / "pages/Auth/Login.tsx").read_text(encoding="utf-8")
    if 'useState<SignInMode>("identify")' not in login or "handleIdentifySubmit" not in login:
        problems.append("login is not email-first")
    return "; ".join(problems) or None


OFFLINE = [
    ("D13 all three upload paths enforce the workspace list (ARCH40-S2)", g_d13_all_paths),
    ("ARCH-38 idempotency: edited files recognised as superseded", g_arch38_superseded),
    ("D36 enums serialised by value in API responses", g_d36_enum_serialisation),
    ("D21 panel / email-first login present in the frontend", g_frontend_t3),
]


def _real_auth_client():
    """A real owner, a real session, a signed token (no dependency overrides)."""
    from sqlalchemy import text
    from fastapi.testclient import TestClient

    import app.main
    from app.core.security import create_access_token
    from app.db.session import SessionLocal
    from app.services.session_service import create_session

    with SessionLocal() as db:
        cols = [r[0] for r in db.execute(text("select column_name from information_schema.columns where table_name='users'")).all()]
        uid, oid, wid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        slug = f"t3-{uid.hex[:8]}"
        db.execute(text("insert into users (id,email,hashed_password,is_active,is_superuser,timezone,locale) values (:i,:e,'x',true,false,'UTC','en-US')"), {"i": uid, "e": f"{slug}@gates.flowpilot-hardening.dev"})
        for c in cols:
            if "verified" in c and c.endswith("_at"):
                db.execute(text(f"update users set {c}=now() where id=:i"), {"i": uid})
        tier = db.execute(text("select id from quota_tiers where key='enterprise' order by version desc limit 1")).scalar_one_or_none()
        db.execute(text("insert into organizations (id,slug,name,status,quota_tier_id) values (:i,:s,'hardening-t3-gate','ACTIVE',:t)"), {"i": oid, "s": slug, "t": tier})
        db.execute(text("insert into organization_members (id,organization_id,user_id,role,status) values (:i,:o,:u,'OWNER','ACTIVE')"), {"i": uuid.uuid4(), "o": oid, "u": uid})
        db.execute(text("insert into workspaces (id,workspace_name,timezone,language,currency,date_format,organization_id,slug,status) values (:i,'t3 ws','UTC','en','USD','YYYY-MM-DD',:o,:s,'ACTIVE')"), {"i": wid, "o": oid, "s": slug})
        db.commit()
        issued = create_session(db, user_id=uid)
        db.commit()
        token = create_access_token(subject=str(uid), session_id=issued.session_id, authenticated_at=issued.session.authenticated_at)
    return TestClient(app.main.app, raise_server_exceptions=False), {"Authorization": f"Bearer {token}"}, oid, wid


def db_gates() -> None:
    client, H, oid, wid = _real_auth_client()

    def d21() -> Optional[str]:
        from app.db.session import SessionLocal
        from app.services.automation import rule_triggers

        W = f"/api/v1/workspaces/{wid}/assertions"
        r = client.post(f"{W}/clause-checks", headers=H, json={"name": "Termination notice"})
        if r.status_code != 201:
            return f"create {r.status_code} {r.text[:150]}"
        rid = r.json()["rule_id"]
        if client.patch(f"{W}/clause-checks/{rid}", headers=H, json={"is_active": True}).status_code != 409:
            return "a check with no clause could be switched on"
        body = {"node_key": "clause_check", "sentence": "The contract must allow termination with at least 30 days written notice.", "threshold": 0.6, "acknowledge_llm": True}
        r = client.put(f"{W}/rules/{rid}/nodes/clause_check", headers=H, json=body)
        if r.status_code != 200:
            return f"definition save {r.status_code} {r.text[:150]}"
        if client.patch(f"{W}/clause-checks/{rid}", headers=H, json={"is_active": True}).status_code != 200:
            return "activation failed after the clause was saved"
        with SessionLocal() as db:
            selected = [str(x.id) for x in rule_triggers.active_rules_for_event(db, workspace_id=wid, event_type="work_item.enriched")]
        if rid not in selected:
            return "the engine does not select the clause check for work_item.enriched"
        if client.delete(f"{W}/clause-checks/{rid}", headers=H).status_code != 204:
            return "delete failed"
        return None

    run_gate("D21 clause check: create, guard, save clause, activate, engine selects it, delete", d21)

    def d36() -> Optional[str]:
        r = client.post(f"/api/v1/organizations/{oid}/identity/domains", headers=H, json={"domain": f"t3-{uuid.uuid4().hex[:6]}-gate.com"})
        if r.status_code not in (200, 201):
            return f"domain create {r.status_code} {r.text[:120]}"
        status = r.json().get("status")
        return None if status == "PENDING" else f"status serialised as {status!r}"

    run_gate("D36 identity domain status serialised as a plain value", d36)


def mutation_gates() -> None:
    def expect_fail(label: str, gate: Callable[[], Optional[str]], patch: Callable[[], Callable[[], None]]) -> None:
        undo = patch()
        try:
            try:
                outcome = gate()
            except Exception as exc:  # noqa: BLE001
                outcome = type(exc).__name__
        finally:
            undo()
        record(f"mutation killed: {label}", outcome is not None, "gate still PASSED with the defect present")

    def m_d36():
        probe = HERE / "app/api/__mutation_probe_t3__.py"
        probe.write_text("def f(row):\n    return {'status': str(row.status)}\n", encoding="utf-8")
        return lambda: probe.unlink()

    expect_fail("D36 str(enum) reintroduced", g_d36_enum_serialisation, m_d36)

    def m_d13():
        target = HERE / "app/api/v1/ingestion.py"
        original = target.read_bytes()
        text = original.decode("utf-8").replace("ARCH40-S2:", "ARCH40-SX:")
        target.write_bytes(text.encode("utf-8"))
        return lambda: target.write_bytes(original)

    expect_fail("D13 superseding sentinel removed", g_d13_all_paths, m_d13)


def _run(cmd: list[str], cwd: pathlib.Path, timeout: int = 3600) -> tuple[int, str]:
    shell = os.name == "nt"
    p = subprocess.run(cmd if not shell else " ".join(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=shell)
    return p.returncode, (p.stdout + p.stderr)[-600:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ("db", "mutation", "frontend", "previous", "chain", "all"):
        parser.add_argument(f"--{flag}", action="store_true")
    args = parser.parse_args()
    if args.all:
        args.db = args.mutation = args.frontend = args.previous = args.chain = True
    import app.main  # noqa: F401

    print("\n=== Tier 3 offline gates ===")
    for name, fn in OFFLINE:
        run_gate(name, fn)
    if args.db:
        print("\n=== Tier 3 real-auth HTTP gates ===")
        db_gates()
    if args.mutation:
        print("\n=== Tier 3 mutation gates ===")
        mutation_gates()
    if args.frontend:
        print("\n=== Frontend build gates ===")
        for label, cmd in (("tsc --noEmit", ["npx", "tsc", "--noEmit", "-p", "tsconfig.json"]),
                           ("eslint --max-warnings=0", ["npx", "eslint", "src", "--max-warnings=0"]),
                           ("vite production build", ["npx", "vite", "build"])):
            rc, out = _run(cmd, FRONTEND, timeout=900)
            record(f"frontend: {label}", rc == 0, out.strip().splitlines()[-1] if rc and out.strip() else "")
    if args.previous:
        print("\n=== Tier 1 and tier 2 harnesses (re-run) ===")
        flags = [f for f, on in (("--db", args.db), ("--mutation", args.mutation)) if on]
        for script in ("verify_hardening_tier1.py", "verify_hardening_tier2.py"):
            rc, out = _run([sys.executable, script, *flags], HERE)
            record(f"{script} {' '.join(flags)}", rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
        if args.chain:
            rc, out = _run([sys.executable, "verify_hardening_tier1.py", "--chain"] + (["--db"] if args.db else []), HERE)
            record("milestone chain ARCH-31..40 (via tier-1 harness)", rc == 0, out.strip().splitlines()[-1] if out.strip() else "")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nRESULT: {passed}/{len(RESULTS)} passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

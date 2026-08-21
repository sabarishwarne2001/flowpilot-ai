#!/usr/bin/env python3
"""ARCH-13 release gate.

    python scripts/verify_arch13.py             # static + database
    python scripts/verify_arch13.py --static    # no database needed (CI)
    python scripts/verify_arch13.py --json

Static:
  - No module under `app/services/tools/` imports `fenced_context`.
  - `assert_tool_boundary()` passes with the selectors registered.
  - INTERNAL_EVENT_TYPES ∩ WEBHOOK_EVENT_TYPES == ∅.
  - The migration's vocabulary CHECK matches the Python vocabulary.
  - No route imports `executor.run_execution` (automation runs from a job, not
    a request -- the ARCH-14 `publish` precedent).
  - `enrich.py` no longer calls the automation service inline (F3).

Database:
  - No `automation_executions` in RUNNING past its `deadline_at`.
  - No execution with `spent_cost_micros > budget_cost_micros`.
  - No `correlation_id` chain containing the same `rule_id` twice with status
    other than SUPPRESSED_CYCLE.
  - No `outbox_events` row with `depth > AUTOMATION_MAX_DEPTH`.
  - No INTERNAL outbox event was ever fanned out to a customer endpoint.
  - No `document_verifications` in PENDING older than the enrichment timeout.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "app"
TOOLS_DIR = APP / "services" / "tools"
ROUTES_DIR = APP / "api"

FORBIDDEN_IN_ROUTES = ("run_execution", "reap_stranded")

FENCED_CONTEXT_MODULES = (
    "app.services.fenced_context",
    "fenced_context",
)

PASS, FAIL, PENDING = "PASS", "FAIL", "PENDING"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    findings: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status == FAIL


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _python_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, UnicodeDecodeError):
        return None


# =====================================================================
# Static — R33
# =====================================================================


def check_tools_do_not_import_fenced_context() -> Check:
    findings: list[str] = []
    for path in _python_files(TOOLS_DIR):
        tree = _parse(path)
        if tree is None:
            findings.append(f"{_rel(path)}: could not be parsed")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FENCED_CONTEXT_MODULES:
                        findings.append(f"{_rel(path)}:{node.lineno} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module in FENCED_CONTEXT_MODULES:
                    disallowed = [
                        a.name
                        for a in node.names
                        if a.name not in ("register_tool_selector",)
                    ]
                    if disallowed:
                        findings.append(
                            f"{_rel(path)}:{node.lineno} imports "
                            f"{', '.join(disallowed)} from {module}"
                        )

    if findings:
        return Check(
            "tools_do_not_import_fenced_context",
            FAIL,
            "A tool selector can reach retrieved document content (R33).",
            findings,
        )
    return Check(
        "tools_do_not_import_fenced_context",
        PASS,
        f"{len(list(_python_files(TOOLS_DIR)))} file(s) under app/services/tools/ are clean.",
    )


def check_tool_boundary_holds() -> Check:
    try:
        from app.services import tools  # noqa: F401
        from app.services.tools import action_selectors  # noqa: F401
        from app.services.fenced_context import (
            TOOL_SELECTORS,
            assert_tool_boundary,
        )

        assert_tool_boundary()
    except Exception as exc:  # noqa: BLE001
        return Check(
            "tool_boundary_holds",
            FAIL,
            f"{type(exc).__name__}: {exc}",
        )
    return Check(
        "tool_boundary_holds",
        PASS,
        f"{len(TOOL_SELECTORS)} selector(s) registered and verified: "
        + ", ".join(sorted(TOOL_SELECTORS)),
    )


def check_no_route_runs_an_execution() -> Check:
    findings: list[str] = []
    for path in _python_files(ROUTES_DIR):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and "executor" in (node.module or ""):
                for alias in node.names:
                    if alias.name in FORBIDDEN_IN_ROUTES:
                        findings.append(
                            f"{_rel(path)}:{node.lineno} imports {alias.name}"
                        )
            elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_IN_ROUTES:
                findings.append(f"{_rel(path)}:{node.lineno} calls .{node.attr}")

    if findings:
        return Check(
            "no_route_runs_an_execution",
            FAIL,
            "A request handler can walk an automation graph synchronously.",
            findings,
        )
    return Check("no_route_runs_an_execution", PASS, "No route reaches the executor.")


# =====================================================================
# Static — F1 / F3
# =====================================================================


def check_vocabularies_disjoint() -> Check:
    try:
        from app.core.automation_events import INTERNAL_EVENT_TYPES
        from app.core.webhook_events import WEBHOOK_EVENT_TYPES
    except Exception as exc:  # noqa: BLE001
        return Check("vocabularies_disjoint", FAIL, f"{type(exc).__name__}: {exc}")

    overlap = sorted(INTERNAL_EVENT_TYPES & WEBHOOK_EVENT_TYPES)
    if overlap:
        return Check(
            "vocabularies_disjoint",
            FAIL,
            "An internal event type is in the webhook vocabulary.",
            overlap,
        )
    return Check(
        "vocabularies_disjoint",
        PASS,
        f"{len(INTERNAL_EVENT_TYPES)} internal / {len(WEBHOOK_EVENT_TYPES)} public, disjoint.",
    )


def check_migration_vocabulary_matches_python() -> Check:
    migration = (
        REPO_ROOT / "alembic" / "versions" / "arch13_step1_outbox_internal_events.py"
    )
    if not migration.exists():
        return Check(
            "migration_vocabulary_matches_python", FAIL, "Step 13.1 migration missing."
        )

    tree = _parse(migration)
    if tree is None:
        return Check(
            "migration_vocabulary_matches_python", FAIL, "Migration could not be parsed."
        )

    declared: set[str] = set()
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        elif isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        else:
            continue
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "INTERNAL_EVENT_TYPES":
                try:
                    declared = set(ast.literal_eval(value))
                    found = True
                except (ValueError, SyntaxError):
                    pass

    if not found:
        return Check(
            "migration_vocabulary_matches_python",
            FAIL,
            "Could not read INTERNAL_EVENT_TYPES from migration.",
        )

    from app.core.automation_events import INTERNAL_EVENT_TYPES

    if declared != set(INTERNAL_EVENT_TYPES):
        return Check(
            "migration_vocabulary_matches_python",
            FAIL,
            "The migration's vocabulary CHECK and automation_events.py disagree.",
            [
                f"only in migration: {sorted(declared - set(INTERNAL_EVENT_TYPES))}",
                f"only in python:    {sorted(set(INTERNAL_EVENT_TYPES) - declared)}",
            ],
        )
    return Check(
        "migration_vocabulary_matches_python",
        PASS,
        f"{len(declared)} event types agree between the CHECK and the frozenset.",
    )


def check_enrich_does_not_call_automation_inline() -> Check:
    path = APP / "workers" / "handlers" / "enrich.py"
    if not path.exists():
        return Check("enrich_uses_outbox", FAIL, "enrich.py not found.")

    tree = _parse(path)
    if tree is None:
        return Check("enrich_uses_outbox", FAIL, "enrich.py could not be parsed.")

    findings = [
        f"{_rel(path)}:{node.lineno} calls execute_rules_for_work_item"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "execute_rules_for_work_item"
    ]
    if findings:
        return Check(
            "enrich_uses_outbox",
            FAIL,
            "Automation is still triggered by an inline call (F3).",
            findings,
        )

    source = path.read_text(encoding="utf-8", errors="ignore")
    if "emit_internal" not in source:
        return Check(
            "enrich_uses_outbox",
            FAIL,
            "enrich.py does not emit an internal outbox event.",
        )
    return Check(
        "enrich_uses_outbox", PASS, "Enrichment triggers automation via the outbox."
    )


# =====================================================================
# Database
# =====================================================================


def _table_exists(db, name: str) -> bool:
    from sqlalchemy import text

    return bool(
        db.execute(
            text("SELECT to_regclass(:n) IS NOT NULL"), {"n": f"public.{name}"}
        ).scalar()
    )


def check_db_no_stranded_executions(db) -> Check:
    from sqlalchemy import text

    if not _table_exists(db, "automation_executions"):
        return Check("db_no_stranded_executions", PENDING, "Step 13.3 not applied.")

    rows = db.execute(
        text(
            "SELECT id, rule_id, deadline_at FROM automation_executions "
            "WHERE status = 'RUNNING' AND deadline_at < now() LIMIT 20"
        )
    ).fetchall()
    if rows:
        return Check(
            "db_no_stranded_executions",
            FAIL,
            "Executions are RUNNING past their deadline.",
            [f"execution={r.id} rule={r.rule_id} deadline={r.deadline_at}" for r in rows],
        )
    return Check("db_no_stranded_executions", PASS, "No stranded executions.")


def check_db_no_budget_breach(db) -> Check:
    from sqlalchemy import text

    if not _table_exists(db, "automation_executions"):
        return Check("db_no_budget_breach", PENDING, "Step 13.3 not applied.")

    rows = db.execute(
        text(
            "SELECT id, spent_cost_micros, budget_cost_micros "
            "FROM automation_executions "
            "WHERE spent_cost_micros > budget_cost_micros LIMIT 20"
        )
    ).fetchall()
    if rows:
        return Check(
            "db_no_budget_breach",
            FAIL,
            "A6 breached.",
            [f"execution={r.id} spent={r.spent_cost_micros} budget={r.budget_cost_micros}" for r in rows],
        )
    return Check("db_no_budget_breach", PASS, "Every execution is within its budget.")


def check_db_budget_constraint_installed(db) -> Check:
    from sqlalchemy import text

    if not _table_exists(db, "automation_executions"):
        return Check("db_budget_constraint_installed", PENDING, "Step 13.3 not applied.")

    present = {
        row.conname
        for row in db.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'automation_executions'::regclass"
            )
        ).fetchall()
    }
    has_spend_budget = any("spend" in c and "budget" in c for c in present)
    has_deadline = any("deadl" in c for c in present)

    missing: list[str] = []
    if not has_spend_budget:
        missing.append("ck_automation_executions_spend_within_budget")
    if not has_deadline:
        missing.append("ck_automation_executions_deadline_matches_status")

    if missing:
        return Check(
            "db_budget_constraint_installed",
            FAIL,
            "A6 constraints are missing.",
            missing,
        )
    return Check(
        "db_budget_constraint_installed", PASS, "A6 constraints are installed."
    )


def check_db_no_undetected_cycles(db) -> Check:
    from sqlalchemy import text

    if not _table_exists(db, "automation_executions"):
        return Check("db_no_undetected_cycles", PENDING, "Step 13.3 not applied.")

    rows = db.execute(
        text(
            "SELECT correlation_id, rule_id, count(*) AS runs "
            "FROM automation_executions "
            "WHERE status NOT IN ('SUPPRESSED_CYCLE', 'SUPPRESSED_DEPTH') "
            "GROUP BY correlation_id, rule_id HAVING count(*) > 1 LIMIT 20"
        )
    ).fetchall()
    if rows:
        return Check(
            "db_no_undetected_cycles",
            FAIL,
            "Cycle detection is not firing.",
            [f"chain={r.correlation_id} rule={r.rule_id} runs={r.runs}" for r in rows],
        )
    return Check(
        "db_no_undetected_cycles", PASS, "No rule ran twice in one causal chain."
    )


def check_db_depth_bounded(db) -> Check:
    from sqlalchemy import text
    from app.core.config import settings

    rows = db.execute(
        text("SELECT id, depth FROM outbox_events WHERE depth > :m LIMIT 20"),
        {"m": int(settings.AUTOMATION_MAX_DEPTH)},
    ).fetchall()
    if rows:
        return Check(
            "db_depth_bounded",
            FAIL,
            f"Events exceed AUTOMATION_MAX_DEPTH ({settings.AUTOMATION_MAX_DEPTH}).",
            [f"event={r.id} depth={r.depth}" for r in rows],
        )
    return Check(
        "db_depth_bounded",
        PASS,
        f"No event exceeds depth {settings.AUTOMATION_MAX_DEPTH}.",
    )


def check_db_no_internal_event_delivered(db) -> Check:
    from sqlalchemy import text

    if not _table_exists(db, "webhook_deliveries"):
        return Check("db_no_internal_event_delivered", PENDING, "No webhook table.")

    rows = db.execute(
        text(
            "SELECT d.id, e.event_type FROM webhook_deliveries d "
            "JOIN outbox_events e ON e.id = d.outbox_event_id "
            "WHERE e.visibility = 'INTERNAL' LIMIT 20"
        )
    ).fetchall()
    if rows:
        return Check(
            "db_no_internal_event_delivered",
            FAIL,
            "An INTERNAL event was fanned out to a customer endpoint.",
            [f"delivery={r.id} event_type={r.event_type}" for r in rows],
        )
    return Check(
        "db_no_internal_event_delivered",
        PASS,
        "No internal event has ever reached a customer endpoint.",
    )


def check_db_no_stranded_verifications(db) -> Check:
    from sqlalchemy import text

    if not _table_exists(db, "document_verifications"):
        return Check("db_no_stranded_verifications", PENDING, "Step 13.7 not applied.")

    from app.core.config import settings

    timeout = int(getattr(settings, "AUTOMATION_EXECUTION_TIMEOUT_S", 120)) * 10
    rows = db.execute(
        text(
            "SELECT id, work_item_id, created_at FROM document_verifications "
            "WHERE status = 'PENDING' "
            "AND created_at < now() - make_interval(secs => :s) LIMIT 20"
        ),
        {"s": timeout},
    ).fetchall()
    if rows:
        return Check(
            "db_no_stranded_verifications",
            FAIL,
            "Verifications are stuck in PENDING.",
            [f"verification={r.id} work_item={r.work_item_id} since={r.created_at}" for r in rows],
        )
    return Check("db_no_stranded_verifications", PASS, "No stranded verifications.")


def run(static_only: bool) -> list[Check]:
    checks = [
        check_tools_do_not_import_fenced_context(),
        check_tool_boundary_holds(),
        check_no_route_runs_an_execution(),
        check_vocabularies_disjoint(),
        check_migration_vocabulary_matches_python(),
        check_enrich_does_not_call_automation_inline(),
    ]
    if static_only:
        return checks

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        checks.extend(
            [
                check_db_budget_constraint_installed(db),
                check_db_no_budget_breach(db),
                check_db_no_stranded_executions(db),
                check_db_no_undetected_cycles(db),
                check_db_depth_bounded(db),
                check_db_no_internal_event_delivered(db),
                check_db_no_stranded_verifications(db),
            ]
        )
    finally:
        db.close()
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static", action="store_true", help="skip database checks")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    checks = run(args.static)

    if args.as_json:
        print(
            json.dumps(
                [
                    {
                        "name": c.name,
                        "status": c.status,
                        "detail": c.detail,
                        "findings": c.findings,
                    }
                    for c in checks
                ],
                indent=2,
            )
        )
    else:
        width = max(len(c.name) for c in checks)
        print("ARCH-13 release gate\n" + "=" * 72)
        for check in checks:
            print(f"{check.status:<8} {check.name:<{width}}  {check.detail}")
            for finding in check.findings:
                print(f"           - {finding}")
            if check.failed and check.detail:
                print()
        failed = [c for c in checks if c.failed]
        pending = [c for c in checks if c.status == PENDING]
        print("=" * 72)
        print(
            f"{len(checks) - len(failed) - len(pending)} passed, "
            f"{len(failed)} failed, {len(pending)} pending"
        )

    return 1 if any(c.failed for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
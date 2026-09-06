"""Exhaustive seam scanner — every insert on a UNIQUE-constrained model.

    python scripts/scan_idempotency_seams.py            # full report
    python scripts/scan_idempotency_seams.py --gate     # exit 1 on unreviewed sites
    python scripts/scan_idempotency_seams.py --json

Why this exists
---------------
Hand-listing "every call site that inserts into a table with a UNIQUE
constraint" does not survive the next commit. This walks the AST instead:
it reads every model's `__table_args__` and column definitions to build the
set of unique-constrained tables, then finds every `db.add(X)` where X is
constructed from one of them, and reports whether the enclosing function has
any conflict handling.

The classification is the point
-------------------------------
An unguarded insert is NOT automatically a defect. There are two populations
and they need opposite treatment:

  RETRY_PATH   Reached from a worker, a relay, or a scheduled job. The same
               logical operation is replayed on failure, so a UniqueViolation
               is a routine collision. get-or-return is correct: return the
               row the first attempt wrote.

  USER_INTENT  Reached from an HTTP route as a deliberate create. A
               UniqueViolation means the caller asked for something that
               already exists, and the correct answer is 409 Conflict.

Applying get-or-return blindly to the second population is not a fix, it is
a vulnerability. `crud/user.py:create_user` inserts into `users`, which has
a unique index on `email`. Making that get-or-return means a signup with an
existing address silently returns the existing account, and the attacker is
now holding a session for someone else's user. The same reasoning applies to
`create_invitation` (token_hash), `create_api_key` (secret_hash) and
`issue_token` (token_hash), where a returned existing row hands the caller a
credential they did not generate.

So this script classifies rather than merely counting. RETRY_PATH sites
without a guard are defects. USER_INTENT sites without a guard are correct as
written and are recorded here so that a future reviewer does not "fix" them.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

RETRY_PATH = "RETRY_PATH"
USER_INTENT = "USER_INTENT"
UNREVIEWED = "UNREVIEWED"

#: Sites reached from a worker, relay, or scheduled job. A replay of the same
#: logical operation must return the existing row, not raise.
KNOWN_RETRY_PATH: frozenset[str] = frozenset({
    "app/services/outbox_service.py::emit",
    "app/services/job_service.py::enqueue",
    "app/services/notification/outbox_dispatcher.py::dispatch",
    "app/services/automation/executor.py::create_execution",
    "app/services/partner/rev_share_service.py::compute_period",
    "app/services/usage_service.py::record_usage",
    "app/services/slo_service.py::record_measurement",
    "app/services/reconciliation/engine.py::persist_statement",
    "app/workers/handlers/verification.py::handle_document_verify",
    "app/workers/handlers/enrich.py::_ensure_workspace_defaults",
    "app/services/billing/invoice_service.py::assemble",
    "app/services/billing/account_service.py::ensure_billing_account",
    "app/services/compliance/erasure_service.py::erase_subject",
    "app/services/document_intake_service.py::ingest_validated",
    "app/services/supplier_reconciliation_service.py::ingest_invoice",
})

#: Deliberate creates from an HTTP route. A UniqueViolation here is the
#: correct outcome and must surface as 409. Do NOT add conflict handling.
KNOWN_USER_INTENT: frozenset[str] = frozenset({
    "app/crud/user.py::create_user",
    "app/crud/api_key.py::create_api_key",
    "app/crud/organization_invitation.py::create_invitation",
    "app/crud/ownership_transfer.py::create_transfer",
    "app/crud/ai_settings.py::create_ai_settings",
    "app/crud/document_settings.py::create_document_settings",
    "app/crud/email_settings.py::create_email_settings",
    "app/crud/work_item.py::create_work_item",
    "app/services/auth_token_service.py::issue_token",
    "app/services/session_service.py::create_session",
    "app/services/email_change_service.py::request_email_change",
    "app/services/analytics/sync_service.py::create_destination",
    "app/services/analytics/sync_service.py::create_schedule",
    "app/services/byok/credential_service.py::upsert_credential",
    "app/services/byok/model_routing_service.py::upsert_route",
    "app/services/partner/marketplace_service.py::install_manifest",
    "app/services/partner/tenancy_service.py::add_member",
    "app/services/pricing_service.py::publish",
    "app/services/quota_service.py::publish_tier",
    "app/services/slo_service.py::set_target",
    "app/services/slo_service.py::seed_platform_defaults",
    "app/services/spend_control_service.py::set_limit",
    "app/api/v1/partner.py::create_agreement",
    "app/crud/organization.py::create_organization",
    "app/services/branding/domain_service.py::claim_domain",
    "app/services/partner/marketplace_service.py::create_item",
    "app/services/partner/marketplace_service.py::publish_manifest",
    "app/services/partner/tenancy_service.py::create_partner",
    "app/services/partner/tenancy_service.py::assign_organization",
    "app/services/partner/tenancy_service.py::register_signing_key",
})

GUARD_MARKERS = ("begin_nested", "on_conflict", "IntegrityError")


@dataclass
class Site:
    path: str
    function: str
    line: int
    model: str
    table: str
    constraints: str
    guarded: bool
    classification: str

    @property
    def key(self) -> str:
        return f"{self.path}::{self.function}"

    @property
    def is_defect(self) -> bool:
        return self.classification == RETRY_PATH and not self.guarded


def _unique_models() -> tuple[dict[str, str], dict[str, list[str]]]:
    """model class -> table, and model class -> its unique constraints."""
    model_table: dict[str, str] = {}
    model_uniques: dict[str, list[str]] = {}

    for path in sorted((BACKEND / "app" / "models").glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            table: str | None = None
            uniques: list[str] = []
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if not isinstance(target, ast.Name):
                            continue
                        if target.id == "__tablename__" and isinstance(stmt.value, ast.Constant):
                            table = stmt.value.value
                        elif target.id == "__table_args__":
                            rendered = ast.unparse(stmt.value)
                            named = re.findall(r'"(uq_[a-z0-9_]+)"', rendered)
                            uniques.extend(named)
                            if "unique=True" in rendered and not named:
                                uniques.append("<unnamed unique index>")
                if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                    rendered = ast.unparse(stmt.value)
                    if "unique=True" in rendered and isinstance(stmt.target, ast.Name):
                        uniques.append(f"<column {stmt.target.id}>")
            if table:
                model_table[node.name] = table
                if uniques:
                    model_uniques[node.name] = uniques

    return model_table, model_uniques


def _scan() -> list[Site]:
    model_table, model_uniques = _unique_models()
    sites: list[Site] = []

    for path in sorted((BACKEND / "app").rglob("*.py")):
        relative = path.relative_to(BACKEND).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            rendered = ast.unparse(fn)
            guarded = any(marker in rendered for marker in GUARD_MARKERS)

            # Track `x = Model(...)` so `db.add(x)` resolves to a class.
            assigned: dict[str, str] = {}
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
                    continue
                func = node.value.func
                cls = (
                    func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute)
                    else None
                )
                if not cls:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned[target.id] = cls

            for node in ast.walk(fn):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add"
                    and node.args
                ):
                    continue
                arg = node.args[0]
                cls: str | None = None
                if isinstance(arg, ast.Name):
                    cls = assigned.get(arg.id)
                elif isinstance(arg, ast.Call):
                    func = arg.func
                    cls = (
                        func.id if isinstance(func, ast.Name)
                        else func.attr if isinstance(func, ast.Attribute)
                        else None
                    )
                if not cls or cls not in model_uniques:
                    continue

                key = f"{relative}::{fn.name}"
                classification = (
                    RETRY_PATH if key in KNOWN_RETRY_PATH
                    else USER_INTENT if key in KNOWN_USER_INTENT
                    else UNREVIEWED
                )
                sites.append(
                    Site(
                        path=relative,
                        function=fn.name,
                        line=node.lineno,
                        model=cls,
                        table=model_table[cls],
                        constraints=", ".join(model_uniques[cls]),
                        guarded=guarded,
                        classification=classification,
                    )
                )

    return sites


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scan_idempotency_seams")
    parser.add_argument("--gate", action="store_true", help="exit 1 on defects or unreviewed sites")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    sites = _scan()
    if args.as_json:
        print(json.dumps([asdict(s) for s in sites], indent=2))
        return 0

    buckets: dict[str, list[Site]] = defaultdict(list)
    for site in sites:
        buckets[site.classification].append(site)

    defects = [s for s in sites if s.is_defect]
    unreviewed = buckets[UNREVIEWED]

    print(f"Inserts on UNIQUE-constrained models: {len(sites)}")
    print(f"  retry-path:  {len(buckets[RETRY_PATH])}  ({len(defects)} unguarded -> DEFECTS)")
    print(f"  user-intent: {len(buckets[USER_INTENT])}  (unguarded is correct; 409 is the answer)")
    print(f"  unreviewed:  {len(unreviewed)}")

    if defects:
        print("\n--- DEFECTS: retry-path inserts with no conflict handling ---")
        for site in sorted(defects, key=lambda s: (s.path, s.line)):
            print(f"  {site.path}:{site.line}  {site.function}()")
            print(f"      {site.model} [{site.table}]  {site.constraints}")

    if unreviewed:
        print("\n--- UNREVIEWED: classify these in KNOWN_RETRY_PATH or KNOWN_USER_INTENT ---")
        for site in sorted(unreviewed, key=lambda s: (s.path, s.line)):
            flag = "guarded" if site.guarded else "UNGUARDED"
            print(f"  [{flag}] {site.path}:{site.line}  {site.function}()  -> {site.model}")

    if defects or unreviewed:
        if args.gate:
            print(
                f"\nGATE FAILED: {len(defects)} defect(s), {len(unreviewed)} unreviewed.",
                file=sys.stderr,
            )
            return 1
        return 0

    print("\nNo unguarded retry-path inserts and nothing unreviewed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
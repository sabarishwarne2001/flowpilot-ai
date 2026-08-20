#!/usr/bin/env python3
"""ARCH-14 release gate.

    python scripts/verify_arch14.py             # static + database
    python scripts/verify_arch14.py --static    # no database needed (CI)
    python scripts/verify_arch14.py --json
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

TENANT_COST_FIELDS = frozenset(
    {"input_cost_per_1k_tokens", "output_cost_per_1k_tokens"}
)

TENANT_COST_ALLOWLIST: dict[str, str] = {
    "app/models/ai_settings.py": "column declaration (CONTRACT: dropped in 14.8)",
    "app/schemas/ai_settings.py": "response compatibility (CONTRACT: dropped in 14.8)",
    "app/api/v1/ai_settings.py": "response compatibility router (CONTRACT: dropped in 14.8)",
    "app/services/ai_settings_service.py": (
        "serves the field from the price book for one release "
        "(CONTRACT: dropped in 14.8)"
    ),
    "app/services/workspace_service.py": (
        "writes 0.0 at workspace creation; never read back "
        "(CONTRACT: dropped in 14.8)"
    ),
    "app/services/pricing_service.py": "owns pricing",
}

USAGE_UPDATE_ALLOWLIST = frozenset(
    {
        "app/services/rollup_service.py",
        "app/workers/handlers/rollup.py",
    }
)

RECONCILIATION_DIR = "app/services/reconciliation"


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
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _parse(path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return None


def check_no_tenant_cost_reads() -> Check:
    findings: list[str] = []

    for path in _python_files(APP):
        rel = _rel(path)
        if rel in TENANT_COST_ALLOWLIST:
            continue
        tree = _parse(path)
        if tree is None:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in TENANT_COST_FIELDS:
                findings.append(f"{rel}:{node.lineno} attribute .{node.attr}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in TENANT_COST_FIELDS:
                    findings.append(
                        f"{rel}:{node.lineno} string literal {node.value!r} (getattr / dict key)"
                    )

    if findings:
        return Check(
            "no_tenant_cost_reads",
            FAIL,
            "A tenant-writable price field is referenced outside the allowlist.",
            findings,
        )
    return Check(
        "no_tenant_cost_reads",
        PASS,
        f"no references outside {len(TENANT_COST_ALLOWLIST)} allowlisted modules",
    )


def check_no_usage_event_updates() -> Check:
    findings: list[str] = []

    for path in _python_files(APP):
        rel = _rel(path)
        if rel in USAGE_UPDATE_ALLOWLIST:
            continue
        tree = _parse(path)
        if tree is None:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                lowered = " ".join(node.value.lower().split())
                if "update usage_events" in lowered:
                    findings.append(f"{rel}:{node.lineno} raw UPDATE usage_events")
                if "delete from usage_events" in lowered:
                    findings.append(f"{rel}:{node.lineno} raw DELETE usage_events")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"update", "delete"} and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Name) and first.id == "UsageEvent":
                        findings.append(
                            f"{rel}:{node.lineno} sqlalchemy {node.func.id}(UsageEvent)"
                        )

    if findings:
        return Check(
            "no_usage_event_updates",
            FAIL,
            "usage_events is financial evidence and append-only.",
            findings,
        )
    return Check("no_usage_event_updates", PASS, "ledger is append-only in app code")


def check_publish_has_no_http_surface() -> Check:
    findings: list[str] = []
    api_root = APP / "api"

    if api_root.exists():
        for path in _python_files(api_root):
            tree = _parse(path)
            if tree is None:
                continue
            rel = _rel(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if "pricing_service" in node.module:
                        names = {a.name for a in node.names}
                        if "publish" in names:
                            findings.append(f"{rel}:{node.lineno} imports publish")
                if isinstance(node, ast.Attribute) and node.attr == "publish":
                    value = node.value
                    if isinstance(value, ast.Name) and "pricing" in value.id:
                        findings.append(
                            f"{rel}:{node.lineno} calls pricing_service.publish"
                        )

    if findings:
        return Check(
            "publish_has_no_http_surface",
            FAIL,
            "A route can publish prices. Publication belongs in scripts/seed_price_book.py.",
            findings,
        )
    return Check("publish_has_no_http_surface", PASS, "no route reaches publish()")


def check_usage_api_reads_rollups_only() -> Check:
    targets = [
        APP / "api" / "v1" / "usage.py",
        APP / "services" / "usage_metrics_service.py",
    ]
    findings: list[str] = []
    for path in targets:
        if not path.exists():
            return Check(
                "usage_api_reads_rollups_only", PENDING, "Step 14.7 not shipped"
            )
        tree = _parse(path)
        if tree is None:
            continue
        rel = _rel(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "UsageEvent":
                findings.append(f"{rel}:{node.lineno} references UsageEvent")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "usage_events" in node.value.lower():
                    findings.append(f"{rel}:{node.lineno} names usage_events")

    if findings:
        return Check(
            "usage_api_reads_rollups_only",
            FAIL,
            "The usage API reads the ledger. It must read rollups only.",
            findings,
        )
    return Check("usage_api_reads_rollups_only", PASS, "rollups only")


def check_tier_publish_has_no_http_surface() -> Check:
    findings: list[str] = []
    api_root = APP / "api"
    if api_root.exists():
        for path in _python_files(api_root):
            tree = _parse(path)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if "quota_service" in node.module:
                        if {"publish_tier", "assign_tier"} & {
                            a.name for a in node.names
                        }:
                            findings.append(f"{_rel(path)}:{node.lineno}")
                if isinstance(node, ast.Attribute) and node.attr in {
                    "publish_tier",
                    "assign_tier",
                }:
                    value = node.value
                    if isinstance(value, ast.Name) and "quota" in value.id:
                        findings.append(f"{_rel(path)}:{node.lineno}")
    if findings:
        return Check(
            "tier_publish_has_no_http_surface",
            FAIL,
            "A route can publish or assign a quota tier.",
            findings,
        )
    return Check("tier_publish_has_no_http_surface", PASS, "no route reaches it")


def check_reconciliation_never_writes_ledger() -> Check:
    recon = APP / "services" / "reconciliation"
    if not recon.exists():
        return Check(
            "reconciliation_never_writes_ledger", PENDING, "Step 14.5 not shipped"
        )

    findings: list[str] = []
    for path in _python_files(recon):
        tree = _parse(path)
        if tree is None:
            continue
        rel = _rel(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in {"UsageEvent", "record_usage"}:
                findings.append(f"{rel}:{node.lineno} references {node.id}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "usage_events" in node.value.lower():
                    findings.append(f"{rel}:{node.lineno} names usage_events in SQL")

    if findings:
        return Check(
            "reconciliation_never_writes_ledger",
            FAIL,
            "Reconciliation touches the ledger.",
            findings,
        )
    return Check("reconciliation_never_writes_ledger", PASS, "no ledger references")


def check_statement_sources_declare_fidelity() -> Check:
    base = APP / "services" / "reconciliation" / "base.py"
    if not base.exists():
        return Check(
            "statement_sources_declare_fidelity", PENDING, "Step 14.5 not shipped"
        )

    sources_dir = APP / "services" / "reconciliation"
    findings: list[str] = []
    for path in _python_files(sources_dir):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
            if "ProviderStatementSource" not in bases:
                continue
            assigned = {
                t.id
                for stmt in node.body
                if isinstance(stmt, (ast.Assign, ast.AnnAssign))
                for t in (
                    stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                )
                if isinstance(t, ast.Name)
            }
            for required in ("grain", "attribution"):
                if required not in assigned:
                    findings.append(
                        f"{_rel(path)}:{node.lineno} {node.name} omits {required}"
                    )

    if findings:
        return Check(
            "statement_sources_declare_fidelity",
            FAIL,
            "Every consumer downstream branches on grain and attribution.",
            findings,
        )
    return Check("statement_sources_declare_fidelity", PASS, "all sources declare")


def _table_exists(db, name: str) -> bool:
    from sqlalchemy import text

    return bool(
        db.execute(
            text("SELECT to_regclass(:n) IS NOT NULL"), {"n": f"public.{name}"}
        ).scalar()
    )


def check_db_every_row_priced(db) -> Check:
    from sqlalchemy import text

    unpriced = db.execute(
        text(
            """
            SELECT count(*) FROM usage_events
             WHERE price_book_id IS NULL
               AND COALESCE(details->>'price_source', '') <> 'legacy_ai_settings'
            """
        )
    ).scalar_one()

    unavailable = db.execute(
        text(
            """
            SELECT count(*) FROM usage_events
             WHERE COALESCE(details->>'price_unavailable', 'false') = 'true'
            """
        )
    ).scalar_one()

    if unpriced or unavailable:
        return Check(
            "db_every_row_priced",
            FAIL,
            "Rows written since 14.1 carry no price.",
            [
                f"{unpriced} rows unpriced and unmarked",
                f"{unavailable} rows flagged price_unavailable",
            ],
        )
    return Check("db_every_row_priced", PASS, "every post-14.1 row carries a price")


def check_db_cost_consistent(db) -> Check:
    from sqlalchemy import text

    bad = db.execute(
        text(
            """
            SELECT count(*) FROM usage_events
             WHERE unit_price_micros IS NOT NULL
               AND cost_micros IS NOT NULL
               AND cost_micros <> round(quantity * unit_price_micros)
            """
        )
    ).scalar_one()

    constraint = db.execute(
        text(
            """
            SELECT count(*) FROM pg_constraint
             WHERE conname = 'ck_usage_events_cost_matches_unit_price'
            """
        )
    ).scalar_one()

    if bad or not constraint:
        return Check(
            "db_cost_consistent",
            FAIL,
            "A row's cost does not follow from its own recorded unit price.",
            [
                f"{bad} inconsistent rows",
                f"constraint present: {bool(constraint)}",
            ],
        )
    return Check("db_cost_consistent", PASS, "cost == round(quantity * unit_price)")


def check_db_published_books_unmutated(db) -> Check:
    from sqlalchemy import select

    from app.models.price_book import PriceBook
    from app.services import pricing_service

    books = (
        db.execute(select(PriceBook).where(PriceBook.published_at.is_not(None)))
        .scalars()
        .all()
    )
    findings = [
        f"v{b.version} ({b.id}) digest mismatch"
        for b in books
        if not pricing_service.verify_digest(b)
    ]

    if findings:
        return Check(
            "db_published_books_unmutated",
            FAIL,
            "A published book no longer hashes to what it hashed at publish.",
            findings,
        )
    return Check(
        "db_published_books_unmutated", PASS, f"{len(books)} published books verified"
    )


def check_db_triggers_installed(db) -> Check:
    from sqlalchemy import text

    expected = {
        "trg_usage_events_immutable",
        "trg_price_books_publish_immutable",
        "trg_price_book_entries_publish_immutable",
        "trg_usage_rollups_seal_immutable",
        "trg_rollup_windows_seal_immutable",
        "trg_quota_tiers_publish_immutable",
        "trg_quota_tier_entries_publish_immutable",
    }
    present = {
        row[0]
        for row in db.execute(
            text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
        ).all()
    }
    missing = sorted(expected - present)

    disabled = [
        row[0]
        for row in db.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE NOT tgisinternal AND tgenabled = 'D'"
            )
        ).all()
    ]

    if missing or disabled:
        return Check(
            "db_triggers_installed",
            FAIL,
            "An immutability trigger is missing or disabled.",
            [f"missing: {m}" for m in missing] + [f"disabled: {d}" for d in disabled],
        )
    return Check("db_triggers_installed", PASS, "all immutability triggers enabled")


def check_db_no_stranded_unaggregated(db) -> Check:
    if not _table_exists(db, "usage_rollups"):
        return Check("db_no_stranded_unaggregated", PENDING, "Step 14.2 not shipped")

    from sqlalchemy import text
    from app.core.config import settings

    grace = int(getattr(settings, "ROLLUP_SEAL_GRACE_HOURS", 26))
    stranded = db.execute(
        text(
            "SELECT count(*) FROM usage_events "
            "WHERE aggregated_at IS NULL "
            f"AND occurred_at < now() - interval '{grace} hours'"
        )
    ).scalar_one()

    if stranded:
        return Check(
            "db_no_stranded_unaggregated",
            FAIL,
            "Rows older than the seal grace window were never folded into a rollup.",
            [f"{stranded} stranded rows"],
        )
    return Check("db_no_stranded_unaggregated", PASS, "no stranded ledger rows")


def check_db_sealed_rollups_untouched(db) -> Check:
    if not _table_exists(db, "usage_rollups"):
        return Check("db_sealed_rollups_untouched", PENDING, "Step 14.2 not shipped")

    from sqlalchemy import text

    violations = db.execute(
        text(
            "SELECT count(*) FROM usage_rollups "
            "WHERE sealed_at IS NOT NULL AND updated_at > sealed_at"
        )
    ).scalar_one()

    if violations:
        return Check(
            "db_sealed_rollups_untouched",
            FAIL,
            "A sealed bucket was modified after sealing.",
            [f"{violations} mutated sealed buckets"],
        )
    return Check("db_sealed_rollups_untouched", PASS, "seals hold")


def check_db_overage_rows_priced(db) -> Check:
    from sqlalchemy import text

    if not _table_exists(db, "quota_tier_entries"):
        return Check("db_overage_rows_priced", PENDING, "Step 14.4 not shipped")

    unpriced = db.execute(
        text(
            "SELECT count(*) FROM usage_events "
            "WHERE event_type LIKE '%.overage' AND price_book_id IS NULL"
        )
    ).scalar_one()

    orphaned = db.execute(
        text(
            """
            SELECT count(*) FROM quota_tier_entries qte
             WHERE qte.overage_policy = 'ALLOW_AND_BILL'
               AND NOT EXISTS (
                   SELECT 1 FROM price_book_entries pbe
                    WHERE pbe.tier_key = qte.overage_price_tier_key
               )
            """
        )
    ).scalar_one()

    if unpriced or orphaned:
        return Check(
            "db_overage_rows_priced",
            FAIL,
            "An overage line was written without a price, or a live "
            "ALLOW_AND_BILL entry points at an unpriced tier_key.",
            [
                f"{unpriced} unpriced overage rows",
                f"{orphaned} ALLOW_AND_BILL entries with no book entry",
            ],
        )
    return Check("db_overage_rows_priced", PASS, "every overage line is priced")


def run(static_only: bool) -> list[Check]:
    checks = [
        check_no_tenant_cost_reads(),
        check_no_usage_event_updates(),
        check_publish_has_no_http_surface(),
        check_usage_api_reads_rollups_only(),
        check_tier_publish_has_no_http_surface(),
        check_reconciliation_never_writes_ledger(),
        check_statement_sources_declare_fidelity(),
    ]
    if static_only:
        return checks

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        checks.extend(
            [
                check_db_triggers_installed(db),
                check_db_every_row_priced(db),
                check_db_cost_consistent(db),
                check_db_published_books_unmutated(db),
                check_db_no_stranded_unaggregated(db),
                check_db_sealed_rollups_untouched(db),
                check_db_overage_rows_priced(db),
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
        print("ARCH-14 release gate\n" + "=" * 72)
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
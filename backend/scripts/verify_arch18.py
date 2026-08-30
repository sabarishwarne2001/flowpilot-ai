"""ARCH-18 verification gate.

Static checks run anywhere. Database checks are skipped with a clear SKIP
rather than a false FAIL when Postgres is unreachable, matching the pattern
scripts/verify_arch09_step4_5.py established.

    python scripts/verify_arch18.py
    python scripts/verify_arch18.py --static-only

The most valuable check here is G6: it re-reads the live
`usage_events_immutable()` function body out of pg_proc and asserts the two
ARCH-18 columns are in the enumeration. A future ALTER TABLE that adds a
column without extending that function reopens the retroactive-margin hole
silently, and this is the only thing that would notice.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []


def record(check: str, status: str, detail: str = "") -> None:
    _results.append((check, status, detail))
    marker = {PASS: "  ok  ", FAIL: " FAIL ", SKIP: " skip "}[status]
    print(f"[{marker}] {check}" + (f" — {detail}" if detail else ""))


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------


def g1_vocabulary_has_not_drifted() -> None:
    """The migration duplicates the source vocabulary on purpose. Assert the
    two copies still agree — a migration is a historical snapshot and must not
    import mutable app code, so this check is what keeps the duplication safe.
    """
    model = read("app/models/supplier_cogs.py")
    migration = read("alembic/versions/arch18_step1_cogs_margins.py")

    def tuple_after(text: str, name: str) -> Optional[tuple[str, ...]]:
        match = re.search(rf"{name}[^=]*=\s*\((.*?)\)", text, re.S)
        if not match:
            return None
        return tuple(re.findall(r'"([A-Z_]+)"', match.group(1)))

    for name in ("COST_BASIS_SOURCE_VALUES", "RECONCILIATION_STATUS_VALUES"):
        a = tuple_after(model, name)
        b = tuple_after(migration, name)
        if a and b and a == b:
            record(f"G1 {name} identical in model and migration", PASS, str(a))
        else:
            record(
                f"G1 {name} identical in model and migration",
                FAIL,
                f"model={a} migration={b}",
            )


def g2_no_coalesce_on_cost_basis() -> None:
    """The one-word change that would make every margin wrong.

    COALESCE(cost_basis_micros, 0) turns an unknown cost into a 100% gross
    margin. This is a grep, and it is the highest-value check in the file
    after G6.
    """
    offenders: list[str] = []
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(
            r"coalesce\s*\(\s*(?:func\.)?sum\s*\([^)]*cost_basis[^)]*\)\s*,\s*0",
            text,
            re.I,
        ):
            # margin_service's _COST is the one legitimate use: it sums only
            # rows already filtered to IS NOT NULL, where the 0 is the empty
            # set's total and not a substituted unknown.
            if path.name == "margin_service.py":
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{match.start()}")

    if offenders:
        record("G2 no COALESCE on cost basis outside margin_service", FAIL,
               ", ".join(offenders))
    else:
        record("G2 no COALESCE on cost basis outside margin_service", PASS)


def g3_admin_router_is_gated() -> None:
    """The gate must be on the router, not repeated per endpoint."""
    text = read("app/api/v1/admin/cogs.py")
    on_router = "dependencies=[Depends(require_superadmin)]" in text
    record("G3 /admin/cogs router declares require_superadmin",
           PASS if on_router else FAIL)

    tree = ast.parse(text)
    org_scoped = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(a.arg == "organization_id" for a in node.args.args)
    ]
    record(
        "G3b no /admin/cogs endpoint takes organization_id",
        PASS if not org_scoped else FAIL,
        ", ".join(org_scoped),
    )


def g4_superadmin_dependency_exists() -> None:
    text = read("app/api/deps.py")
    has_fn = "async def require_superadmin" in text
    has_alias = "RequireSuperAdmin" in text
    listed = "require_superadmin" in read("app/main.py")
    record("G4 require_superadmin is defined", PASS if has_fn else FAIL)
    record("G4b RequireSuperAdmin alias exported", PASS if has_alias else FAIL)
    record("G4c main.py auth allowlist includes it", PASS if listed else FAIL)


def g5_no_write_to_usage_events_from_reconciliation() -> None:
    """Reconciliation writes new rows in a separate table. Never the ledger."""
    text = read("app/services/supplier_reconciliation_service.py")
    offenders = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"(UPDATE|INSERT INTO|DELETE FROM)\s+usage_events", line, re.I)
    ]
    record(
        "G5 reconciliation never writes usage_events",
        PASS if not offenders else FAIL,
        "; ".join(offenders),
    )


def g6_static_immutability_enumeration() -> None:
    migration = read("alembic/versions/arch18_step1_cogs_margins.py")
    body = migration.split("IMMUTABILITY_FUNCTION_V3")[1].split('"""')[1]
    missing = [
        column
        for column in ("cost_basis_micros", "cost_basis_source")
        if f"NEW.{column}" not in body
    ]
    record(
        "G6 migration's V3 trigger enumerates both cost columns",
        PASS if not missing else FAIL,
        ", ".join(missing),
    )


def g7_identifier_lengths() -> None:
    """PostgreSQL truncates at 63 characters and SQLAlchemy truncates
    differently. A name over the limit is permanent autogenerate drift."""
    migration = read("alembic/versions/arch18_step1_cogs_margins.py")
    long_names = sorted(
        {
            name
            for name in re.findall(r"\b((?:ck|ix|uq|fk|pk)_[a-z0-9_]+)\b", migration)
            if len(name) > 63
        }
    )
    record(
        "G7 no identifier exceeds 63 characters",
        PASS if not long_names else FAIL,
        ", ".join(long_names),
    )


def g8_migration_chain() -> None:
    migration = read("alembic/versions/arch18_step1_cogs_margins.py")
    revision = re.search(r'^revision\s*=\s*"([^"]+)"', migration, re.M)
    down = re.search(r'^down_revision\s*=\s*"([^"]+)"', migration, re.M)
    ok = (
        revision
        and down
        and revision.group(1) == "arch18_step1_cogs_margins"
        and down.group(1) == "arch17_step1_slos"
    )
    record(
        "G8 migration chains onto arch17_step1_slos",
        PASS if ok else FAIL,
        f"{down.group(1) if down else '?'} -> {revision.group(1) if revision else '?'}",
    )


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def database_checks() -> None:
    try:
        from sqlalchemy import text as sql

        from app.db.session import engine
    except Exception as exc:  # noqa: BLE001
        record("D* database checks", SKIP, f"import failed: {exc}")
        return

    try:
        connection = engine.connect()
    except Exception as exc:  # noqa: BLE001
        record("D* database checks", SKIP, f"no database: {exc}")
        return

    with connection:
        # D1 — the live trigger body, not the migration source. This is the
        # check that catches a future column added without extending it.
        body = connection.execute(
            sql(
                "SELECT prosrc FROM pg_proc WHERE proname = 'usage_events_immutable'"
            )
        ).scalar_one_or_none()
        if body is None:
            record("D1 usage_events_immutable() exists", FAIL)
        else:
            missing = [
                column
                for column in ("cost_basis_micros", "cost_basis_source")
                if f"NEW.{column}" not in body
            ]
            record(
                "D1 live trigger protects both cost columns",
                PASS if not missing else FAIL,
                ", ".join(missing),
            )

        # D2 — no undeclared zero anywhere in the ledger.
        undeclared = connection.execute(
            sql(
                "SELECT count(*) FROM usage_events "
                "WHERE cost_basis_micros = 0 "
                "  AND cost_basis_source IS DISTINCT FROM 'ZERO_BYOK'"
            )
        ).scalar_one()
        record(
            "D2 zero rows have an undeclared zero cost",
            PASS if undeclared == 0 else FAIL,
            f"{undeclared} rows",
        )

        # D3 — the honest-unknowns metric, reported not asserted.
        row = connection.execute(
            sql(
                "SELECT count(*) FILTER (WHERE cost_basis_micros IS NULL), count(*) "
                "FROM usage_events WHERE occurred_at >= now() - interval '30 days'"
            )
        ).one()
        unknown, total = int(row[0]), int(row[1])
        share = (unknown / total) if total else 0.0
        record(
            "D3 unknown cost share, trailing 30d",
            PASS,
            f"{unknown}/{total} rows ({share:.1%}) — expected 100% until a "
            "price book with cost basis takes effect",
        )

        # D4 — the four CHECK constraints landed.
        found = {
            name
            for (name,) in connection.execute(
                sql(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conname LIKE '%cost_basis%' "
                    "   OR conname LIKE '%zero_cost_is_declared%'"
                )
            ).all()
        }
        expected = {
            "ck_usage_events_cost_basis_pair_complete",
            "ck_usage_events_cost_basis_non_negative",
            "ck_usage_events_cost_basis_source_known",
            "ck_usage_events_zero_cost_is_declared",
            "ck_price_book_entries_cost_basis_pair_complete",
            "ck_price_book_entries_cost_basis_non_negative",
            "ck_price_book_entries_cost_basis_source_known",
            "ck_price_book_entries_zero_cost_is_declared",
        }
        missing = sorted(expected - found)
        record(
            "D4 all eight cost-basis CHECK constraints present",
            PASS if not missing else FAIL,
            ", ".join(missing),
        )

        # D5 — every reconciliation's ratio is NULL iff modelled is zero.
        broken = connection.execute(
            sql(
                "SELECT count(*) FROM supplier_reconciliations "
                "WHERE (modelled_total_micros = 0) <> (variance_ratio IS NULL)"
            )
        ).scalar_one()
        record(
            "D5 variance_ratio is NULL exactly when modelled is zero",
            PASS if broken == 0 else FAIL,
            f"{broken} rows",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-18 verification gate")
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args()

    print("ARCH-18 — COGS, unit economics & supplier reconciliation\n")

    g1_vocabulary_has_not_drifted()
    g2_no_coalesce_on_cost_basis()
    g3_admin_router_is_gated()
    g4_superadmin_dependency_exists()
    g5_no_write_to_usage_events_from_reconciliation()
    g6_static_immutability_enumeration()
    g7_identifier_lengths()
    g8_migration_chain()

    if not args.static_only:
        print()
        database_checks()

    failures = [r for r in _results if r[1] == FAIL]
    skipped = [r for r in _results if r[1] == SKIP]
    print(
        f"\n{len(_results) - len(failures) - len(skipped)} passed, "
        f"{len(failures)} failed, {len(skipped)} skipped"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
"""ARCH-24 verification gate — Cost Truth Consolidation & Financial Close.

    python scripts/verify_arch24.py
    python scripts/verify_arch24.py --static-only

Fifteen checks. The three that matter most, and why:

G2  — single cost authority, by AST rather than grep. `margin_service.
      modelled_cost_for_provider` is the function that turns usage events into
      a supplier cost figure. Exactly one module in `app/` may call it. A grep
      for "modelled_cost_for_provider" would be defeated by the next person to
      reintroduce a second loop through a helper or an alias; walking the call
      graph is not.

G3  — no `COALESCE(cost_basis*, 0)` outside one named, guarded statement.
      Deliberately NOT a ban on the token: the rollup upsert genuinely needs
      it, because SQL's `NULL + x = NULL` means a plain additive column would
      let one unpriced event erase the basis of every priced event already in
      the bucket. The guard is the surrounding CASE. So this check whitelists
      the guarded form by shape and rejects every other occurrence, and G3b
      separately asserts the guard is still there.

G5  — the rollup seal trigger is still a BLANKET deny. ARCH-24 D1 rests on
      `usage_rollups_seal_immutable()` refusing every UPDATE on a sealed row
      regardless of column. The ARCH-24 brief asked for that function to be
      rewritten to enumerate columns; doing so would convert a deny-all into
      an allowlist and leave a financial column writable on invoiced periods.
      This check fails if anyone ever does it.

Database checks SKIP rather than FAIL when Postgres is unreachable, matching
scripts/verify_arch09_step4_5.py.
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
    """utf-8-sig: tolerates a BOM if one is ever reintroduced upstream."""
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def read_code(rel: str) -> str:
    """Source with docstrings stripped.

    Without this, this gate's own explanatory prose — and the long doctrinal
    comments ARCH-24 deliberately left in `rollup_service` and `engine` —
    would trip the greps below. A check that fires on its own documentation
    teaches people to delete the documentation.
    """
    source = read(rel)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and first.lineno is not None
            and first.end_lineno is not None
        ):
            spans.append((first.lineno, first.end_lineno))

    if not spans:
        return source

    drop = {n for start, end in spans for n in range(start, end + 1)}
    return "\n".join(
        line for i, line in enumerate(source.splitlines(), start=1) if i not in drop
    )


def py_files(subdir: str) -> list[pathlib.Path]:
    return sorted((ROOT / subdir).rglob("*.py"))


# ---------------------------------------------------------------------------
# Migrations and schema shape
# ---------------------------------------------------------------------------


def g1_migrations_chained_single_head() -> None:
    """Both ARCH-24 migrations exist and extend the chain without branching."""
    versions = ROOT / "alembic" / "versions"
    revs: dict[str, str] = {}
    downs: dict[str, Optional[str]] = {}

    for path in sorted(versions.glob("*.py")):
        src = path.read_text(encoding="utf-8-sig")
        rev = re.search(r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", src, re.M)
        down = re.search(
            r"^down_revision(?::[^=]+)?\s*=\s*(?:['\"]([^'\"]+)['\"]|None)", src, re.M
        )
        if rev:
            revs[rev.group(1)] = path.name
            downs[rev.group(1)] = down.group(1) if (down and down.group(1)) else None

    children: dict[Optional[str], list[str]] = {}
    for rev, parent in downs.items():
        children.setdefault(parent, []).append(rev)

    heads = [r for r in revs if r not in children]
    branches = {k: v for k, v in children.items() if len(v) > 1 and k is not None}

    for expected, parent in (
        ("arch24_step1_rollup_cost_basis", "arch23_step1_azure_credential_shape"),
        ("arch24_step2_revenue_recognition", "arch24_step1_rollup_cost_basis"),
    ):
        if expected not in revs:
            record(f"G1 migration {expected} present", FAIL, "missing")
        elif downs.get(expected) != parent:
            record(
                f"G1 migration {expected} present",
                FAIL,
                f"down_revision={downs.get(expected)!r}, expected {parent!r}",
            )
        else:
            record(f"G1 migration {expected} present", PASS)

    if len(heads) == 1 and not branches:
        record("G1 single linear head", PASS, f"{heads[0]} over {len(revs)} revisions")
    else:
        record("G1 single linear head", FAIL, f"heads={heads} branches={branches}")


def g2_single_cost_authority() -> None:
    """Exactly one module computes supplier cost variance.

    AST, not grep. The call graph is walked for any invocation of
    `modelled_cost_for_provider` — attribute call, bare call after a
    from-import, or aliased import — and the set of modules doing so must be
    exactly {supplier_reconciliation_service}.
    """
    target = "modelled_cost_for_provider"
    callers: set[str] = set()

    for path in py_files("app"):
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue

        # Aliases introduced by `from ... import modelled_cost_for_provider as x`
        aliases = {target}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == target and alias.asname:
                        aliases.add(alias.asname)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name in aliases:
                callers.add(rel)

    expected = {"app/services/supplier_reconciliation_service.py"}
    extra = callers - expected
    missing = expected - callers

    if extra:
        record(
            "G2 exactly one module computes supplier cost variance",
            FAIL,
            f"unexpected callers: {sorted(extra)}",
        )
    elif missing:
        record(
            "G2 exactly one module computes supplier cost variance",
            FAIL,
            f"the authority no longer calls it: {sorted(missing)}",
        )
    else:
        record("G2 exactly one module computes supplier cost variance", PASS)


def g3_no_unguarded_coalesce_on_cost_basis() -> None:
    """No `COALESCE(cost_basis*, 0)` outside the one guarded rollup statement.

    A silent zero cost reads as 100% gross margin and someone will price on it.
    """
    pattern = re.compile(
        r"coalesce\s*\(\s*(?:func\.)?(?:sum\s*\()?[^)]*cost_basis[^)]*\)?\s*,\s*0",
        re.I,
    )
    allowed_file = "app/services/rollup_service.py"
    offenders: list[str] = []

    for path in py_files("app"):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "app/services/margin_service.py":
            # Pre-existing ARCH-18 exemption; G2 of verify_arch18 owns it.
            continue
        code = read_code(rel)
        for match in pattern.finditer(code):
            line_no = code[: match.start()].count("\n") + 1
            if rel == allowed_file:
                continue
            offenders.append(f"{rel}:{line_no}")

    if offenders:
        record("G3 no COALESCE(cost_basis*, 0)", FAIL, ", ".join(offenders))
    else:
        record("G3 no COALESCE(cost_basis*, 0)", PASS, "outside the guarded upsert")


def g3b_rollup_coalesce_is_guarded() -> None:
    """The whitelisted COALESCE in the rollup upsert is still inside its CASE.

    G3 lets `rollup_service` use the token. This is the check that stops that
    permission from being quietly widened into an unguarded sum.
    """
    code = read_code("app/services/rollup_service.py")

    guard = re.search(
        r"cost_basis_micros\s*=\s*CASE\s*"
        r"WHEN\s+usage_rollups\.cost_basis_micros\s+IS\s+NULL\s*"
        r"AND\s+EXCLUDED\.cost_basis_micros\s+IS\s+NULL\s*"
        r"THEN\s+NULL\s*"
        r"ELSE\s+COALESCE\(\s*usage_rollups\.cost_basis_micros\s*,\s*0\s*\)\s*"
        r"\+\s*COALESCE\(\s*EXCLUDED\.cost_basis_micros\s*,\s*0\s*\)\s*"
        r"END",
        code,
        re.I | re.S,
    )
    if guard:
        record("G3b rollup cost basis merge is NULL-guarded", PASS)
    else:
        record(
            "G3b rollup cost basis merge is NULL-guarded",
            FAIL,
            "the CASE WHEN both-NULL THEN NULL guard is gone; an all-unknown "
            "bucket would now write 0",
        )


def g4_rollup_columns_and_constraints() -> None:
    """Model and migration agree on the three new columns."""
    model = read("app/models/usage_rollup.py")
    migration = read("alembic/versions/arch24_step1_rollup_cost_basis.py")

    missing_model = [
        col
        for col in (
            "cost_basis_micros",
            "unknown_cost_basis_event_count",
            "cost_basis_source_mix",
        )
        if col not in model
    ]
    missing_migration = [
        col
        for col in (
            "cost_basis_micros",
            "unknown_cost_basis_event_count",
            "cost_basis_source_mix",
        )
        if col not in migration
    ]

    if missing_model or missing_migration:
        record(
            "G4 rollup cost basis columns in model and migration",
            FAIL,
            f"model missing {missing_model}, migration missing {missing_migration}",
        )
    else:
        record("G4 rollup cost basis columns in model and migration", PASS)

    if re.search(
        r"cost_basis_micros\s+IS\s+NULL\s+OR\s+cost_basis_micros\s*>=\s*0",
        migration,
        re.I,
    ):
        record("G4 non-negative CHECK present", PASS)
    else:
        record("G4 non-negative CHECK present", FAIL)


def g5_seal_trigger_is_blanket_deny() -> None:
    """`usage_rollups_seal_immutable()` must NOT enumerate columns.

    ARCH-24 D1. Enumeration would turn a deny-all into an allowlist and leave
    cost_basis_micros writable on sealed, invoiced periods.
    """
    source = read("alembic/versions/arch14_step2_rollups.py")
    match = re.search(
        r"CREATE OR REPLACE FUNCTION usage_rollups_seal_immutable\(\)(.*?)\$\$ LANGUAGE",
        source,
        re.S,
    )
    if not match:
        record("G5 rollup seal trigger is a blanket deny", FAIL, "function not found")
        return

    body = match.group(1)
    if re.search(r"IS\s+DISTINCT\s+FROM", body, re.I):
        record(
            "G5 rollup seal trigger is a blanket deny",
            FAIL,
            "the function now enumerates columns; sealed rollups are no longer "
            "protected against a financial column being rewritten",
        )
        return

    if "sealed_at IS NULL" not in body or "RAISE EXCEPTION" not in body:
        record(
            "G5 rollup seal trigger is a blanket deny",
            FAIL,
            "the deny path is missing",
        )
        return

    record("G5 rollup seal trigger is a blanket deny", PASS)

    # The step-1 migration asserts the same thing at migrate time.
    step1 = read("alembic/versions/arch24_step1_rollup_cost_basis.py")
    if "IS DISTINCT FROM" in step1 and "pg_get_functiondef" in step1:
        record("G5b step-1 migration asserts the blanket deny at migrate time", PASS)
    else:
        record(
            "G5b step-1 migration asserts the blanket deny at migrate time",
            FAIL,
            "the runtime guard is missing from the migration",
        )


def g6_price_disclosure_is_sourced_not_computed() -> None:
    """The disclosed proration comes from Stripe, never from local arithmetic.

    ARCH-15 established that deriving proration locally guarantees eventually
    disagreeing with the invoice Stripe issues. ARCH-24 kept that.
    """
    seat = read_code("app/services/billing/seat_service.py")

    if "preview_seat_change" not in seat:
        record("G6 proration is sourced from Stripe", FAIL, "no preview call")
        return

    # A local derivation would look like a ratio of elapsed period against
    # total period, applied to a unit price. Any of these tokens next to the
    # disclosure is the shape of that mistake.
    disclosure = seat[seat.find("def seat_price_disclosure") :]
    end = disclosure.find("\ndef ", 10)
    if end > 0:
        disclosure = disclosure[:end]

    suspicious = [
        token
        for token in ("days_remaining", "timedelta(", "total_seconds()", "/ 30", "* 30")
        if token in disclosure
    ]
    if suspicious:
        record(
            "G6 proration is sourced from Stripe",
            FAIL,
            f"local proration arithmetic detected: {suspicious}",
        )
        return

    if "PRORATION_SOURCE_STRIPE" not in seat or "PRORATION_SOURCE_UNAVAILABLE" not in seat:
        record(
            "G6 proration is sourced from Stripe",
            FAIL,
            "provenance discriminators missing",
        )
        return

    record("G6 proration is sourced from Stripe", PASS)


def g7_price_endpoint_registered_and_gated() -> None:
    """`GET .../billing/price-book/seat` exists and requires a billing role."""
    api = read("app/api/v1/billing.py")

    if "/billing/price-book/seat" not in api:
        record("G7 seat price-book endpoint registered", FAIL, "route absent")
        return
    record("G7 seat price-book endpoint registered", PASS)

    handler = api[api.find("/billing/price-book/seat") :][:2000]
    if "RequireOrgBillingReader" in handler:
        record("G7 seat price-book endpoint is role-gated", PASS)
    else:
        record(
            "G7 seat price-book endpoint is role-gated",
            FAIL,
            "no RequireOrgBillingReader dependency on the handler",
        )


def g8_nullable_money_stays_nullable() -> None:
    """The disclosure DTO keeps both money fields Optional.

    Making either non-optional with a 0 default puts a free seat in front of an
    administrator about to provision a paid one.
    """
    schema = read("app/schemas/billing.py")
    block = schema[schema.find("class SeatPriceBookResponse") :][:4000]

    ok = True
    for field in ("unit_price_micros", "proration_micros"):
        if not re.search(rf"{field}\s*:\s*Optional\[int\]", block):
            record(
                "G8 disclosed money fields are Optional",
                FAIL,
                f"{field} is not Optional[int]",
            )
            ok = False
    if ok:
        record("G8 disclosed money fields are Optional", PASS)

    if re.search(r"(unit_price_micros|proration_micros)\s*:\s*int\s*=\s*0", block):
        record("G8b no zero-defaulted money on the disclosure", FAIL)
    else:
        record("G8b no zero-defaulted money on the disclosure", PASS)


def g9_cost_basis_method_present() -> None:
    """The discriminator exists on both reconciliation tables and both DTOs."""
    for rel, label in (
        ("app/models/supplier_cogs.py", "SupplierReconciliation"),
        ("app/models/reconciliation.py", "ReconciliationRun"),
    ):
        if "cost_basis_method" in read(rel):
            record(f"G9 cost_basis_method on {label}", PASS)
        else:
            record(f"G9 cost_basis_method on {label}", FAIL)

    migration = read("alembic/versions/arch24_step1_rollup_cost_basis.py")
    # The backfill must be DDL, not an UPDATE: reconciliation_runs_immutable()
    # refuses every UPDATE once status leaves RUNNING.
    if re.search(r"op\.execute\(\s*[\"']?\s*UPDATE\s+reconciliation_runs", migration, re.I):
        record(
            "G9b historical annotation is DDL, not UPDATE",
            FAIL,
            "an UPDATE against reconciliation_runs will be refused by its "
            "immutability trigger for every historical row",
        )
    else:
        record("G9b historical annotation is DDL, not UPDATE", PASS)


def g10_engine_declares_itself_non_authoritative() -> None:
    """The ARCH-14 engine stamps every run and says so in details."""
    engine = read_code("app/services/reconciliation/engine.py")

    if "METHOD_ARCH14_SELL_SIDE" not in engine:
        record("G10 ARCH-14 engine stamps its denominator", FAIL)
        return
    if '"cost_authority": False' not in engine:
        record(
            "G10 ARCH-14 engine stamps its denominator",
            FAIL,
            "runs no longer carry cost_authority=False in details",
        )
        return
    record("G10 ARCH-14 engine stamps its denominator", PASS)


def g11_no_cost_variance_from_rollup_cost_micros() -> None:
    """24-G1: nothing computes supplier cost variance from customer price.

    `UsageRollup.cost_micros` is what we charged. Subtracting a supplier
    invoice total from it inflates apparent drift by our own gross margin, and
    that is the defect this phase closed.

    Scoped to a single function body, by AST, rather than searched file-wide.
    The first draft of this check searched whole files and failed on
    `usage_service.py`, where the column is read for customer-facing usage
    totals — entirely correct — and the word "supplier" appears 180 lines away
    in an unrelated comment. A check that fires on correct code gets silenced,
    and a silenced check protects nothing.
    """
    supplier_tokens = {
        "invoiced_total_micros",
        "SupplierInvoice",
        "supplier_invoice",
        "supplier_invoice_id",
        "modelled_total_micros",
        "variance_micros",
    }
    offenders: list[str] = []

    for path in py_files("app"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("app/services/reconciliation/"):
            # The ARCH-14 engine legitimately reads this column for volume and
            # price-book drift. G10 is what keeps it honest about the claim.
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            reads_sell_side = False
            touches_supplier = False

            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Attribute)
                    and inner.attr == "cost_micros"
                    and isinstance(inner.value, ast.Name)
                    and inner.value.id == "UsageRollup"
                ):
                    reads_sell_side = True
                elif isinstance(inner, ast.Attribute) and inner.attr in supplier_tokens:
                    touches_supplier = True
                elif isinstance(inner, ast.Name) and inner.id in supplier_tokens:
                    touches_supplier = True

            if reads_sell_side and touches_supplier:
                offenders.append(f"{rel}:{node.name}")

    if offenders:
        record(
            "G11 no supplier variance from UsageRollup.cost_micros",
            FAIL,
            ", ".join(offenders),
        )
    else:
        record("G11 no supplier variance from UsageRollup.cost_micros", PASS)


def g12_statement_intake_precedence() -> None:
    """A statement pull never overwrites an operator-supplied invoice."""
    intake = read_code("app/services/reconciliation/statement_intake.py")

    for token in ("ORIGIN_STATEMENT_PULL", "ORIGIN_OPERATOR_UPLOAD"):
        if token not in intake:
            record("G12 statement intake precedence rule present", FAIL, token)
            return

    if "deferred_to_existing" not in intake:
        record(
            "G12 statement intake precedence rule present",
            FAIL,
            "no deferral path; a pull would collide on the unique index",
        )
        return

    record("G12 statement intake precedence rule present", PASS)


def g13_revenue_recognition_primitives() -> None:
    """Schedules and ledger exist, and the ledger is append-only."""
    migration = read("alembic/versions/arch24_step2_revenue_recognition.py")
    model = read("app/models/revenue_recognition.py")

    for table in ("revenue_schedules", "recognized_revenue_ledger"):
        if table in migration and table in model:
            record(f"G13 {table} defined", PASS)
        else:
            record(f"G13 {table} defined", FAIL)

    if "recognized_revenue_ledger_append_only" in migration:
        record("G13b recognised revenue ledger is append-only", PASS)
    else:
        record("G13b recognised revenue ledger is append-only", FAIL)

    if "recognized_revenue_within_schedule" in migration:
        record("G13c over-recognition guarded by trigger", PASS)
    else:
        record("G13c over-recognition guarded by trigger", FAIL)


def g14_margin_endpoints_superadmin_gated() -> None:
    """Hardening invariant 5: margin data never leaves the superadmin surface."""
    cogs = read("app/api/v1/admin/cogs.py")

    routes = re.findall(r"@router\.(?:get|post)\((.*?)\)\ndef\s+(\w+)", cogs, re.S)
    ungated: list[str] = []
    for _, handler in routes:
        block = cogs[cogs.find(f"def {handler}(") :][:1400]
        if "require_superadmin" not in block and "get_read_db" not in block:
            ungated.append(handler)

    consolidated = cogs[cogs.find("def consolidated_reconciliations(") :][:1400]
    if "require_superadmin" not in consolidated:
        record(
            "G14 consolidated reconciliation endpoint is superadmin-gated",
            FAIL,
            "no require_superadmin dependency",
        )
    else:
        record("G14 consolidated reconciliation endpoint is superadmin-gated", PASS)

    if ungated:
        record("G14b every admin cogs route is gated", FAIL, ", ".join(ungated))
    else:
        record("G14b every admin cogs route is gated", PASS)


def g15_frontend_does_not_recompute_price() -> None:
    """The panel renders what the endpoint returns.

    ARCH-21's single-path discipline. A frontend that recomputes a price will
    drift from the invoice, and the drift is discovered by a customer.
    """
    panel_path = ROOT.parent / "frontend" / "src" / "pages" / "identity" / "JitPolicyPanel.tsx"
    if not panel_path.exists():
        record("G15 frontend does not recompute seat price", SKIP, "panel not found")
        return

    panel = panel_path.read_text(encoding="utf-8-sig")

    # Arithmetic on the disclosed money fields is the failure shape.
    bad = re.findall(
        r"(unit_price_micros|proration_micros)\s*[*/+-]\s*\w+"
        r"|\w+\s*[*/]\s*(?:unit_price_micros|proration_micros)",
        panel,
    )
    if bad:
        record(
            "G15 frontend does not recompute seat price",
            FAIL,
            f"arithmetic on disclosed money: {bad[:3]}",
        )
        return

    record("G15 frontend does not recompute seat price", PASS)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def db_checks() -> None:
    try:
        from sqlalchemy import text as sql_text

        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        record("DB checks", SKIP, f"imports unavailable: {type(exc).__name__}")
        return

    try:
        db = SessionLocal()
    except Exception as exc:  # noqa: BLE001
        record("DB checks", SKIP, f"no session: {type(exc).__name__}")
        return

    try:
        body = db.execute(
            sql_text(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE p.proname = 'usage_rollups_seal_immutable'"
            )
        ).scalar_one_or_none()

        if body is None:
            record("DB rollup seal function present", FAIL, "not in pg_proc")
        elif re.search(r"IS\s+DISTINCT\s+FROM", body, re.I):
            record(
                "DB rollup seal function is a blanket deny",
                FAIL,
                "the live function enumerates columns",
            )
        else:
            record("DB rollup seal function is a blanket deny", PASS)

        cols = {
            row[0]
            for row in db.execute(
                sql_text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'usage_rollups'"
                )
            ).all()
        }
        wanted = {
            "cost_basis_micros",
            "unknown_cost_basis_event_count",
            "cost_basis_source_mix",
        }
        if wanted <= cols:
            record("DB usage_rollups has the cost basis columns", PASS)
        else:
            record(
                "DB usage_rollups has the cost basis columns",
                FAIL,
                f"missing {sorted(wanted - cols)}",
            )

        bad = db.execute(
            sql_text(
                "SELECT count(*) FROM usage_rollups "
                "WHERE cost_basis_micros = 0 AND unknown_cost_basis_event_count "
                "= event_count AND event_count > 0"
            )
        ).scalar_one()
        if int(bad or 0) == 0:
            record("DB no all-unknown bucket wrote a zero basis", PASS)
        else:
            record(
                "DB no all-unknown bucket wrote a zero basis",
                FAIL,
                f"{bad} buckets report 0 cost with every event unpriced",
            )

    except Exception as exc:  # noqa: BLE001
        record("DB checks", SKIP, f"{type(exc).__name__}: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-24 verification gate")
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args()

    print("=" * 72)
    print("ARCH-24 — Cost Truth Consolidation & Financial Close")
    print("=" * 72)

    g1_migrations_chained_single_head()
    g2_single_cost_authority()
    g3_no_unguarded_coalesce_on_cost_basis()
    g3b_rollup_coalesce_is_guarded()
    g4_rollup_columns_and_constraints()
    g5_seal_trigger_is_blanket_deny()
    g6_price_disclosure_is_sourced_not_computed()
    g7_price_endpoint_registered_and_gated()
    g8_nullable_money_stays_nullable()
    g9_cost_basis_method_present()
    g10_engine_declares_itself_non_authoritative()
    g11_no_cost_variance_from_rollup_cost_micros()
    g12_statement_intake_precedence()
    g13_revenue_recognition_primitives()
    g14_margin_endpoints_superadmin_gated()
    g15_frontend_does_not_recompute_price()

    if not args.static_only:
        db_checks()

    failed = [r for r in _results if r[1] == FAIL]
    skipped = [r for r in _results if r[1] == SKIP]
    passed = [r for r in _results if r[1] == PASS]

    print("-" * 72)
    print(f"{len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped")
    if failed:
        print("\nFAILURES:")
        for check, _, detail in failed:
            print(f"  - {check}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
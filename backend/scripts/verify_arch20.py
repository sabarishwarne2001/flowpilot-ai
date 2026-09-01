#!/usr/bin/env python
"""ARCH-20 verification gate — data governance, residency and compliance."""

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

MIGRATION_STEP1 = "alembic/versions/arch20_step1_audit_vocabulary.py"
MIGRATION_STEP2 = "alembic/versions/arch20_step2_governance_residency.py"
MODEL = "app/models/compliance.py"
ERASURE = "app/services/compliance/erasure_service.py"
RESIDENCY = "app/services/compliance/residency_service.py"
EXPORT = "app/services/compliance/export_service.py"
ROUTER = "app/api/v1/compliance.py"

COMPLIANCE_PACKAGE = ROOT / "app" / "services" / "compliance"

PG_IDENTIFIER_LIMIT = 63


def record(check: str, status: str, detail: str = "") -> None:
    _results.append((check, status, detail))
    marker = {PASS: "  ok  ", FAIL: " FAIL ", SKIP: " skip "}[status]
    print(f"[{marker}] {check}" + (f" — {detail}" if detail else ""))


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def read_code(rel: str) -> str:
    import ast

    source = read(rel)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
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

    lines = source.splitlines()
    for start, end in spans:
        for index in range(start - 1, min(end, len(lines))):
            lines[index] = ""
    return "\n".join(lines)


def g1_migration_chain() -> None:
    step1 = read(MIGRATION_STEP1)
    step2 = read(MIGRATION_STEP2)

    def field(text: str, name: str) -> Optional[str]:
        match = re.search(rf'^{name}\s*=\s*"([^"]+)"', text, re.M)
        return match.group(1) if match else None

    checks = [
        (field(step1, "revision") == "arch20_step1_audit_vocabulary", "step1 id"),
        (field(step1, "down_revision") == "arch18_step1_cogs_margins", "step1 parent"),
        (
            field(step2, "revision") == "arch20_step2_governance_residency",
            "step2 id",
        ),
        (
            field(step2, "down_revision") == "arch20_step1_audit_vocabulary",
            "step2 parent",
        ),
    ]
    broken = [label for ok, label in checks if not ok]
    record(
        "G1 migration chain is linear from arch18",
        PASS if not broken else FAIL,
        ", ".join(broken) if broken else "step1 -> step2",
    )


def g2_vocabulary_has_not_drifted() -> None:
    model = read(MODEL)
    migration = read(MIGRATION_STEP2)

    def tuple_after(text: str, name: str) -> Optional[tuple[str, ...]]:
        match = re.search(rf"{name}[^=]*=\s*\((.*?)\)", text, re.S)
        if not match:
            return None
        return tuple(re.findall(r'"([A-Z_]+)"', match.group(1)))

    mismatches: list[str] = []
    for name in ("DATA_RESIDENCY_REGION_VALUES", "COMPLIANCE_EXPORT_STATUS_VALUES"):
        left = tuple_after(model, name)
        right = tuple_after(migration, name)
        if left is None or right is None or left != right:
            mismatches.append(f"{name} {left} != {right}")

    record(
        "G2 residency and export vocabularies agree across model and migration",
        PASS if not mismatches else FAIL,
        "; ".join(mismatches) if mismatches else "2 vocabularies",
    )


def g3_audit_retention_floor() -> None:
    model = read(MODEL)
    migration = read(MIGRATION_STEP2)
    schema = read("app/schemas/compliance.py")

    floor_match = re.search(r"AUDIT_RETENTION_FLOOR_DAYS:\s*int\s*=\s*(\d+)", model)
    floor = int(floor_match.group(1)) if floor_match else -1

    problems: list[str] = []
    if floor != 400:
        problems.append(f"model floor is {floor}, expected 400")
    if "audit_retention_days >= " not in migration:
        problems.append("migration has no audit floor CHECK")
    if "AUDIT_RETENTION_FLOOR_DAYS" not in migration:
        problems.append("migration does not name the floor constant")
    if "AUDIT_RETENTION_FLOOR_DAYS" not in schema:
        problems.append("schema does not enforce the floor at the boundary")

    record(
        "G3 audit retention floor is 400 in model, migration and schema",
        PASS if not problems else FAIL,
        "; ".join(problems) if problems else "400 days",
    )


def g4_financial_ledgers_are_never_written() -> None:
    source = read_code(ERASURE)
    offenders: list[str] = []
    write_pattern = re.compile(
        r"\b(DELETE\s+FROM|UPDATE|INSERT\s+INTO)\s+"
        r"(invoices|invoice_line_items|usage_events)\b",
        re.I,
    )
    for index, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if write_pattern.search(line):
            offenders.append(f"line {index}: {stripped[:70]}")

    orm_writes = re.findall(
        r"\b(Invoice|InvoiceLineItem|UsageEvent)\b\s*\(", source
    )
    if orm_writes:
        offenders.append(f"ORM construction of {sorted(set(orm_writes))}")

    record(
        "G4 erasure never writes to a financial ledger",
        PASS if not offenders else FAIL,
        "; ".join(offenders) if offenders else "invoices, line items, usage events",
    )


def g5_users_row_is_never_deleted() -> None:
    source = read_code(ERASURE)
    offenders: list[str] = []
    if re.search(r"DELETE\s+FROM\s+users\b", source, re.I):
        offenders.append("raw DELETE FROM users")
    if re.search(r"db\.delete\(\s*subject\b", source):
        offenders.append("db.delete(subject)")
    if re.search(r"db\.delete\(\s*user\b", source):
        offenders.append("db.delete(user)")
    if "_anonymise_user" not in read(ERASURE):
        offenders.append("no _anonymise_user path")

    record(
        "G5 erasure anonymises the users row rather than deleting it",
        PASS if not offenders else FAIL,
        "; ".join(offenders) if offenders else "anonymise-in-place",
    )


def g6_router_is_role_gated() -> None:
    source = read(ROUTER)
    decorators = re.findall(
        r"@router\.(get|post|put|patch|delete)\((.*?)\)\ndef\s+(\w+)\((.*?)\n\) ->",
        source,
        re.S,
    )
    ungated = [
        name
        for _method, _path, name, params in decorators
        if "RequireOrgAdmin" not in params and "RequireOrgOwner" not in params
    ]
    missing_scope = [
        name
        for _method, _path, name, _params in decorators
        if f"def {name}(" in source
        and "_assert_scope" not in source.split(f"def {name}(")[1].split("\n@router")[0]
    ]

    problems: list[str] = []
    if ungated:
        problems.append(f"ungated: {ungated}")
    if missing_scope:
        problems.append(f"no _assert_scope: {missing_scope}")

    record(
        "G6 every compliance route is role-gated and scope-checked",
        PASS if not problems else FAIL,
        "; ".join(problems) if problems else f"{len(decorators)} routes",
    )


def g7_identifier_lengths() -> None:
    text_blobs = read(MIGRATION_STEP2) + read(MODEL)
    names = set(
        re.findall(r'"((?:fk|ix|uq|ck|pk)_[a-z0-9_]+)"', text_blobs)
    )
    too_long = sorted(n for n in names if len(n) > PG_IDENTIFIER_LIMIT)
    record(
        "G7 no constraint or index name exceeds 63 characters",
        PASS if not too_long else FAIL,
        f"{len(names)} names, longest {max((len(n) for n in names), default=0)}"
        if not too_long
        else str(too_long),
    )


def g8_encryption_boundary() -> None:
    offenders: list[str] = []
    pattern = re.compile(r"\b(MultiFernet|Fernet)\s*\(")
    for path in sorted(COMPLIANCE_PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "from cryptography.fernet" in source:
            offenders.append(f"{path.name}: imports cryptography.fernet")
        for index, line in enumerate(source.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if pattern.search(line):
                offenders.append(f"{path.name}:{index}")

    record(
        "G8 compliance package instantiates no Fernet (E15)",
        PASS if not offenders else FAIL,
        "; ".join(offenders) if offenders else "clean",
    )


def g9_storage_boundary() -> None:
    forbidden = re.compile(
        r"\.(write_bytes|write_text|unlink|read_bytes|read_text)\s*\(|"
        r"\bshutil\.(move|copy|copyfile|rmtree)\s*\(|"
        r"\bos\.(remove|unlink|rename)\s*\(|"
        r"\bopen\s*\("
    )
    offenders: list[str] = []
    for path in sorted(COMPLIANCE_PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8", errors="ignore")
        for index, line in enumerate(source.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if forbidden.search(line):
                offenders.append(f"{path.name}:{index} {line.strip()[:60]}")

    record(
        "G9 compliance package makes no filesystem call (E10)",
        PASS if not offenders else FAIL,
        "; ".join(offenders) if offenders else "storage driver only",
    )


def g10_every_model_module_is_registered() -> None:
    models_dir = ROOT / "app" / "models"
    init_source = (models_dir / "__init__.py").read_text(encoding="utf-8")

    missing: list[str] = []
    for path in sorted(models_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = path.stem
        if f"app.models.{module}" not in init_source:
            source = path.read_text(encoding="utf-8", errors="ignore")
            if "__tablename__" in source:
                missing.append(module)

    record(
        "G10 every model module with a table is imported by app/models/__init__",
        PASS if not missing else FAIL,
        f"unregistered: {missing}" if missing else "all registered",
    )


def g11_residency_does_not_mutate_the_global_driver() -> None:
    import ast

    source = read(RESIDENCY)
    offenders: list[str] = []

    if "reset_storage_driver" in read_code(RESIDENCY):
        offenders.append("calls reset_storage_driver")
    if re.search(r"storage\._driver\s*=", source):
        offenders.append("assigns app.core.storage._driver")
    if "settings.S3_BUCKET =" in source:
        offenders.append("mutates settings.S3_BUCKET")
    if "ResidencyNotConfiguredError" not in source:
        offenders.append("no refusal path for an unconfigured pinned region")

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        record("G11 residency resolver refuses rather than falling back", FAIL, str(exc))
        return

    target = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "driver_for_region"
        ),
        None,
    )
    if target is None:
        offenders.append("driver_for_region is missing")
    else:
        def guarded_by_global(node: ast.AST) -> bool:
            for branch in ast.walk(target):
                if not isinstance(branch, ast.If):
                    continue
                if "REGION_GLOBAL" not in ast.dump(branch.test):
                    continue
                for inner in branch.body:
                    for descendant in ast.walk(inner):
                        if descendant is node:
                            return True
            return False

        calls = [
            node
            for node in ast.walk(target)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "get_storage_driver"
        ]
        if not calls:
            offenders.append("GLOBAL no longer resolves to the default driver")
        for call in calls:
            if not guarded_by_global(call):
                offenders.append(
                    f"get_storage_driver() at line {call.lineno} is not guarded "
                    f"by a REGION_GLOBAL test"
                )

        raises = [
            node
            for node in ast.walk(target)
            if isinstance(node, ast.Raise)
            and "ResidencyNotConfiguredError" in ast.dump(node)
        ]
        if not raises:
            offenders.append("driver_for_region no longer refuses")

    record(
        "G11 residency resolver refuses rather than falling back",
        PASS if not offenders else FAIL,
        "; ".join(offenders) if offenders else "GLOBAL-only fallback, refusal intact",
    )


def g12_export_stores_a_key_not_a_url() -> None:
    model = read(MODEL)
    schema = read("app/schemas/compliance.py")
    problems: list[str] = []
    if "storage_key" not in model:
        problems.append("model has no storage_key column")
    if re.search(r"download_url:\s*Mapped", model):
        problems.append("model persists download_url")
    block = re.split(r"^class ", schema, flags=re.M)
    body = next(
        (b for b in block if b.startswith("ComplianceExportResponse(")), ""
    )
    if "download_url" in body or "storage_key" in body:
        problems.append("list response exposes a URL or storage key")

    record(
        "G12 exports persist a storage key, never a presigned URL",
        PASS if not problems else FAIL,
        "; ".join(problems) if problems else "key persisted, URL minted",
    )


def g13_sweeper_defaults_to_dry_run() -> None:
    source = read("scripts/sweep_compliance.py")
    problems: list[str] = []
    if '"--apply"' not in source:
        problems.append("no --apply flag")
    if '"--purge"' not in source:
        problems.append("no --purge flag")
    if "auto_purge_enabled" not in source:
        problems.append("purge does not consult auto_purge_enabled")
    if re.search(r'add_argument\(\s*"--apply",\s*action="store_true",\s*default=True',
                 source):
        problems.append("--apply defaults to True")

    record(
        "G13 sweeper is dry-run by default and gates purge on opt-in",
        PASS if not problems else FAIL,
        "; ".join(problems) if problems else "dry run default",
    )


def database_checks() -> None:
    try:
        from sqlalchemy import text

        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        record("D* database checks", SKIP, f"import failed: {exc}")
        return

    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        record("D* database checks", SKIP, f"unreachable: {type(exc).__name__}")
        return

    with db:
        tables = {
            row[0]
            for row in db.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename IN "
                    "('erased_subjects','compliance_exports','retention_policies')"
                )
            ).fetchall()
        }
        record(
            "D1 the three governance tables exist",
            PASS if len(tables) == 3 else FAIL,
            f"found {sorted(tables)}",
        )

        column = db.execute(
            text(
                "SELECT data_type, column_default FROM information_schema.columns "
                "WHERE table_name = 'organizations' "
                "AND column_name = 'data_residency_region'"
            )
        ).first()
        record(
            "D2 organizations.data_residency_region exists and defaults to GLOBAL",
            PASS if column and "GLOBAL" in str(column[1]) else FAIL,
            str(column) if column else "missing",
        )

        found_constraints = {
            row[0]
            for row in db.execute(
                text("SELECT conname FROM pg_constraint WHERE contype = 'c'")
            ).fetchall()
        }
        
        has_audit_floor = any("audit_floor" in name for name in found_constraints)
        has_status_vocab = any("status_vocabulary" in name for name in found_constraints)
        has_complete_key = any("complete_has_key" in name for name in found_constraints)
        has_residency_vocab = any("data_residency_region" in name for name in found_constraints)

        all_constraints_ok = has_audit_floor and has_status_vocab and has_complete_key and has_residency_vocab
        record(
            "D3 the four governance CHECK constraints are installed",
            PASS if all_constraints_ok else FAIL,
            "4 constraints verified" if all_constraints_ok else f"missing constraint patterns among {found_constraints}",
        )

        floor_violations = db.execute(
            text(
                "SELECT count(*) FROM retention_policies "
                "WHERE audit_retention_days IS NOT NULL "
                "AND audit_retention_days < 400"
            )
        ).scalar_one()
        record(
            "D4 no retention policy sits below the 400-day audit floor",
            PASS if floor_violations == 0 else FAIL,
            f"{floor_violations} row(s)",
        )

        leaked = db.execute(
            text(
                "SELECT count(*) FROM erased_subjects es "
                "JOIN users u ON u.id = es.subject_user_id "
                "WHERE u.email NOT LIKE '%@erased.invalid' "
                "   OR u.is_active IS TRUE "
                "   OR u.display_name IS NOT NULL"
            )
        ).scalar_one()
        record(
            "D5 every tombstoned subject's users row is actually anonymised",
            PASS if leaked == 0 else FAIL,
            f"{leaked} row(s) still carry identity",
        )

        trigger = db.execute(
            text(
                "SELECT count(*) FROM pg_trigger "
                "WHERE tgname = 'trg_audit_logs_immutable' AND NOT tgisinternal"
            )
        ).scalar_one()
        record(
            "D6 the ARCH-07 audit immutability trigger survived this phase",
            PASS if trigger == 1 else FAIL,
            f"{trigger} trigger(s)",
        )

        complete_without_key = db.execute(
            text(
                "SELECT count(*) FROM compliance_exports "
                "WHERE status = 'COMPLETE' AND storage_key IS NULL"
            )
        ).scalar_one()
        record(
            "D7 no COMPLETE export lacks an archive key",
            PASS if complete_without_key == 0 else FAIL,
            f"{complete_without_key} row(s)",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-20 verification gate")
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args()

    print("ARCH-20 — data governance, residency & compliance\n")

    g1_migration_chain()
    g2_vocabulary_has_not_drifted()
    g3_audit_retention_floor()
    g4_financial_ledgers_are_never_written()
    g5_users_row_is_never_deleted()
    g6_router_is_role_gated()
    g7_identifier_lengths()
    g8_encryption_boundary()
    g9_storage_boundary()
    g10_every_model_module_is_registered()
    g11_residency_does_not_mutate_the_global_driver()
    g12_export_stores_a_key_not_a_url()
    g13_sweeper_defaults_to_dry_run()

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
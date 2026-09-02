#!/usr/bin/env python
"""Unmask the REAL error behind an `InFailedSqlTransaction` during alembic upgrade.

    python scripts/diagnose_migration_abort.py --to 4fb2e9a4f15c
    python scripts/diagnose_migration_abort.py --to head --database-url postgresql://...

Repository: https://github.com/sabarishwarne2001/flowpilot-ai/tree/main
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import traceback
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Safeguard Windows stdout encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")

FIX_PATTERNS = """
────────────────────────────────────────────────────────────────────────────
FIX PATTERNS — replace swallowed-exception DDL with one of these
────────────────────────────────────────────────────────────────────────────

❌ WRONG — aborts the transaction, then hides it:

    try:
        op.drop_constraint("uq_something", "organizations")
    except Exception:
        pass

✅ (a) INSPECT FIRST — preferred; explicit and readable

    from sqlalchemy import inspect

    bind = op.get_bind()
    insp = inspect(bind)

    existing = {c["name"] for c in insp.get_unique_constraints("organizations")}
    if "uq_something" in existing:
        op.drop_constraint("uq_something", "organizations", type_="unique")

    cols = {c["name"] for c in insp.get_columns("organizations")}
    if "tenant_slug" not in cols:
        op.add_column("organizations", sa.Column("tenant_slug", sa.String(100)))

    idx = {i["name"] for i in insp.get_indexes("organizations")}
    if "ix_org_slug" in idx:
        op.drop_index("ix_org_slug", table_name="organizations")

    if "legacy_table" in insp.get_table_names():
        op.drop_table("legacy_table")

    exists = bind.execute(sa.text(
        "SELECT 1 FROM pg_constraint WHERE conname = :n"
    ), {"n": "ck_organizations_something"}).scalar()
    if exists:
        op.drop_constraint("ck_organizations_something", "organizations")

✅ (b) IF EXISTS / IF NOT EXISTS — terse, PostgreSQL does the check

    op.execute("ALTER TABLE organizations DROP CONSTRAINT IF EXISTS uq_something")
    op.execute("DROP INDEX IF EXISTS ix_org_slug")
    op.execute("DROP TABLE IF EXISTS legacy_table")

✅ (c) SAVEPOINT — only when you genuinely must attempt-and-recover

    bind = op.get_bind()
    try:
        with bind.begin_nested():          # emits SAVEPOINT
            op.drop_constraint("uq_something", "organizations")
    except Exception:
        pass                                # ROLLBACK TO SAVEPOINT already ran
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Unmask a migration abort")
    parser.add_argument("--to", default="head", help="target revision")
    parser.add_argument(
        "--database-url",
        default=None,
        help="overrides alembic.ini / env; use your TEST_DB_NAME database",
    )
    parser.add_argument("--tail", type=int, default=8, help="statements to show")
    args = parser.parse_args()

    try:
        import sqlalchemy as sa
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import event
    except ImportError as exc:
        print(f"[SKIP] {exc}")
        return 2

    alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    if args.database_url:
        alembic_cfg.set_main_option("sqlalchemy.url", args.database_url)
        os.environ["DATABASE_URL"] = args.database_url

    statements: list[str] = []
    first_error: dict[str, Any] = {}

    @event.listens_for(sa.engine.Engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip())

    @event.listens_for(sa.engine.Engine, "handle_error")
    def _capture(exception_context):
        if first_error:
            return
        orig = exception_context.original_exception
        if type(orig).__name__ == "InFailedSqlTransaction":
            return
        first_error["exception"] = orig
        first_error["statement"] = (exception_context.statement or "").strip()
        first_error["parameters"] = exception_context.parameters

    print(f"Running upgrade to {args.to} with statement capture...\n")
    failed = False
    try:
        command.upgrade(alembic_cfg, args.to)
    except Exception:
        failed = True
        print("── upgrade raised ─────────────────────────────────────────────")
        traceback.print_exc()
        print()

    if first_error:
        print("╔══════════════════════════════════════════════════════════════╗")
        print("║  TRUE ROOT CAUSE (captured below the migration's try/except) ║")
        print("╚══════════════════════════════════════════════════════════════╝")
        exc = first_error["exception"]
        print(f"  {type(exc).__module__}.{type(exc).__name__}: {exc}")
        print("\n  Failing statement:")
        for line in first_error["statement"].splitlines():
            print(f"    {line}")
        if first_error.get("parameters"):
            print(f"\n  Parameters: {first_error['parameters']}")
        print()
        if not failed:
            print(
                "  ⚠️  The upgrade REPORTED SUCCESS despite this error, which\n"
                "      means a try/except in the migration swallowed it. The\n"
                "      migration is now stamped as applied while having only\n"
                "      partially run. Fix the swallow, not just the statement.\n"
            )
    elif failed:
        print("  No pre-abort driver error captured. Read the traceback above.\n")
    else:
        print("✅ Upgrade completed with no captured errors.\n")

    if statements:
        print(f"── last {min(args.tail, len(statements))} statement(s) issued ──")
        for s in statements[-args.tail :]:
            first_line = s.splitlines()[0] if s.splitlines() else s
            print(f"  {first_line[:150]}")
        print()

    if failed or first_error:
        print(FIX_PATTERNS)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

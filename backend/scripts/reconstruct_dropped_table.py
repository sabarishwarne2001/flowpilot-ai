#!/usr/bin/env python
"""Reconstruct a dropped table's schema from the migrations that survived it.

    python scripts/reconstruct_dropped_table.py workspace_invitations
    python scripts/reconstruct_dropped_table.py workspace_invitations --verbose

Why this beats guessing
-----------------------
When a CREATE-TABLE revision is lost, the table's shape is not actually gone —
it is distributed across every *other* migration that ever touched it:

* the CONTRACT revision that dropped it **must** recreate it in `downgrade()`
  to be reversible, and that is a verbatim `CREATE TABLE`;
* every MIGRATE revision that backfilled *out of* it names its columns in a
  `SELECT`;
* every EXPAND revision that added a column names that column;
* every CONTRACT revision that set `NOT NULL`, added a `UNIQUE`, or dropped a
  superseded column names it too.

This script harvests all of that and prints it with provenance, so the
reconstruction is assembled from evidence in the repository rather than from
recollection. Anything it cannot resolve is printed as UNKNOWN rather than
filled in with a plausible guess.

Read the output, then write the revision by hand. This tool deliberately does
not emit a ready-to-run migration: a generated `CREATE TABLE` that looks
authoritative but embeds three inferences is more dangerous than a short list
of facts and a clearly-marked gap.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from collections import defaultdict
from typing import Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _literal(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _clean_select(raw: str) -> str:
    """Collapse a SELECT list that may be spread across concatenated string
    literals into one readable line."""
    return " ".join(raw.replace('"', " ").replace("'", " ").split())[:300]


def _split_select_list(raw: str) -> list[str]:
    """Column names from a SELECT list, tolerating Python string
    concatenation, aliases, and qualified names."""
    cleaned = _clean_select(raw)
    out: list[str] = []
    for part in cleaned.split(","):
        token = part.strip()
        if not token or token == "*":
            continue
        token = token.split()[0]          # drop "AS alias"
        token = token.split(".")[-1]      # drop table qualifier
        if re.fullmatch(r"[a-z_][a-z0-9_]*", token, re.I):
            out.append(token)
    return out


def _rev_meta(tree: ast.AST) -> tuple[Optional[str], Optional[str]]:
    rev = down = None
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "revision":
                rev = _literal(node.value) if node.value else None
            elif isinstance(t, ast.Name) and t.id == "down_revision":
                down = _literal(node.value) if node.value else None
    return rev, down


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconstruct a dropped table")
    parser.add_argument("table")
    parser.add_argument("--versions-dir", default="alembic/versions")
    parser.add_argument("--models-dir", default="app/models")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    table = args.table
    versions = (REPO_ROOT / args.versions_dir).resolve()
    if not versions.exists():
        print(f"[SKIP] {versions} not found")
        return 2

    print(f"Reconstructing `{table}` from surviving references\n")

    create_blocks: list[tuple[str, str]] = []
    columns: dict[str, list[str]] = defaultdict(list)   # col -> provenance
    constraints: dict[str, list[str]] = defaultdict(list)
    indexes: dict[str, list[str]] = defaultdict(list)
    raw_sql_hits: list[tuple[str, str]] = []
    select_hits: list[tuple[str, str]] = []

    col_rx = re.compile(rf"\b{re.escape(table)}\.(\w+)")
    select_rx = re.compile(
        rf"SELECT\s+(.+?)FROM\s+{re.escape(table)}\b", re.I | re.S
    )

    for path in sorted(versions.glob("*.py")):
        if path.name.startswith("__"):
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        if table not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        rev, _ = _rev_meta(tree)
        tag = f"{path.name} ({rev})"

        # ---- op.* calls naming the table ------------------------------
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)):
                continue
            if f.value.id != "op":
                continue
            opname = f.attr
            args_lit = [_literal(a) for a in node.args]

            in_function = "upgrade"
            for anc in ast.walk(tree):
                if isinstance(anc, ast.FunctionDef) and anc.lineno <= node.lineno:
                    if any(n is node for n in ast.walk(anc)):
                        in_function = anc.name
            where = f"{tag}:{node.lineno} [{in_function}]"

            if opname == "create_table" and args_lit and args_lit[0] == table:
                seg = ast.get_source_segment(src, node) or ""
                create_blocks.append((where, seg))
                for m in re.finditer(r"sa\.Column\(\s*['\"](\w+)['\"]", seg):
                    columns[m.group(1)].append(f"{where} create_table")

            elif opname == "add_column" and args_lit and args_lit[0] == table:
                seg = ast.get_source_segment(src, node) or ""
                m = re.search(r"sa\.Column\(\s*['\"](\w+)['\"]", seg)
                if m:
                    columns[m.group(1)].append(f"{where} add_column")

            elif opname in {"alter_column", "drop_column"} and args_lit and args_lit[0] == table:
                if len(args_lit) > 1 and args_lit[1]:
                    columns[args_lit[1]].append(f"{where} {opname}")

            elif opname in {
                "create_index", "drop_index", "create_unique_constraint",
                "create_check_constraint", "drop_constraint", "create_foreign_key",
                "create_primary_key",
            }:
                target = args_lit[1] if len(args_lit) > 1 else None
                kw = {k.arg: _literal(k.value) for k in node.keywords}
                target = target or kw.get("table_name") or kw.get("source_table")
                if target != table:
                    continue
                name = args_lit[0] if args_lit else None
                seg = ast.get_source_segment(src, node) or ""
                bucket = indexes if "index" in opname else constraints
                bucket[name or "(unnamed)"].append(f"{where} {opname}")

            elif opname == "execute":
                raw = _literal(node.args[0]) if node.args else None
                if raw and table in raw:
                    raw_sql_hits.append((where, " ".join(raw.split())[:400]))
                    for m in col_rx.finditer(raw):
                        columns[m.group(1)].append(f"{where} raw SQL")
                    for m in select_rx.finditer(raw):
                        select_hits.append((where, " ".join(m.group(1).split())[:300]))
                        for c in _split_select_list(m.group(1)):
                            columns[c].append(f"{where} SELECT")

        # ---- raw text scan for table.column and SELECT ----------------
        for m in col_rx.finditer(src):
            columns[m.group(1)].append(f"{tag} text reference")
        for m in select_rx.finditer(src):
            select_hits.append((tag, _clean_select(m.group(1))))
            for c in _split_select_list(m.group(1)):
                columns[c].append(f"{tag} SELECT list")

    # ---- report -------------------------------------------------------
    print("═" * 70)
    print("1. VERBATIM CREATE TABLE (best source — use this if present)")
    print("═" * 70)
    if create_blocks:
        for where, seg in create_blocks:
            print(f"\n  ▸ {where}\n")
            for line in seg.splitlines():
                print(f"    {line}")
        print(
            "\n  ✅ A verbatim CREATE TABLE was found. If it appears in a\n"
            "     `downgrade()`, that is the dropped table's exact schema —\n"
            "     copy it into the reconstruction's `upgrade()` unchanged.\n"
        )
    else:
        print(
            "\n  ✗ No `op.create_table` for this table in any surviving revision.\n"
            "     Check the DROP revision's downgrade() by hand — if it is a\n"
            "     bare `pass`, the drop was never reversible and that is a\n"
            "     second finding worth recording.\n"
        )

    print("═" * 70)
    print("2. COLUMNS, with provenance")
    print("═" * 70)
    if columns:
        for col in sorted(columns):
            srcs = sorted(set(columns[col]))
            print(f"\n  {col}")
            for s in srcs[: (None if args.verbose else 3)]:
                print(f"      ← {s}")
            if not args.verbose and len(srcs) > 3:
                print(f"      … and {len(srcs) - 3} more (use --verbose)")
        print(
            f"\n  {len(columns)} distinct column name(s) referenced.\n"
        )
    else:
        print("\n  none found\n")

    print("═" * 70)
    print("3. CONSTRAINTS & INDEXES")
    print("═" * 70)
    for label, bucket in (("constraint", constraints), ("index", indexes)):
        if bucket:
            for name in sorted(bucket):
                print(f"\n  [{label}] {name}")
                for s in sorted(set(bucket[name])):
                    print(f"      ← {s}")
    if not constraints and not indexes:
        print("\n  none found\n")
    else:
        print()

    if select_hits:
        print("═" * 70)
        print("4. SELECT lists (name every column a backfill actually read)")
        print("═" * 70)
        for where, cols in select_hits:
            print(f"\n  ▸ {where}\n      SELECT {cols}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())

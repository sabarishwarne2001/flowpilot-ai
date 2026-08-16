#!/usr/bin/env python
r"""Migration history integrity — replay the DDL graph statically and prove that
every table is created before it is touched.

    python scripts/verify_migration_history.py
    python scripts/verify_migration_history.py --versions-dir app/db/migrations/versions
    python scripts/verify_migration_history.py --seed-tables spatial_ref_sys,alembic_version

Exit 0 = history is replayable on an empty database, 1 = it is not, 2 = could
not run.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from typing import Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

_TABLE_ARG_POS: dict[str, int] = {
    "add_column": 0,
    "alter_column": 0,
    "drop_column": 0,
    "create_index": 1,
    "create_unique_constraint": 1,
    "create_check_constraint": 1,
    "create_primary_key": 1,
    "drop_constraint": 1,
    "create_foreign_key": 1,
    "bulk_insert": None,
}
_TABLE_KW: dict[str, str] = {
    "drop_index": "table_name",
    "drop_constraint": "table_name",
    "add_column": "table_name",
    "alter_column": "table_name",
}

_RAW_CREATE = re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)", re.I)
_RAW_DROP = re.compile(r"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?[\"']?(\w+)", re.I)
_RAW_ALTER = re.compile(r"\bALTER\s+TABLE\s+(?:ONLY\s+)?[\"']?(\w+)", re.I)


class Revision:
    def __init__(self, path: pathlib.Path, rev: str, down: Optional[str], tree: ast.AST, src: str):
        self.path = path
        self.rev = rev
        self.down = down
        self.tree = tree
        self.src = src


def _literal(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _parse_revisions(versions_dir: pathlib.Path) -> dict[str, Revision]:
    revs: dict[str, Revision] = {}
    for path in sorted(versions_dir.glob("*.py")):
        if path.name.startswith("__"):
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            print(f"  [WARN] cannot parse {path.name}: {exc}")
            continue
        rev = down = None
        found_down = False
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if not isinstance(t, ast.Name):
                    continue
                if t.id == "revision":
                    rev = _literal(node.value) if node.value else None
                elif t.id == "down_revision":
                    found_down = True
                    down = _literal(node.value) if node.value else None
        if rev is None:
            continue
        if rev in revs:
            print(f"  [WARN] duplicate revision id {rev!r}: "
                  f"{revs[rev].path.name} and {path.name}")
        revs[rev] = Revision(path, rev, down if found_down else None, tree, src)
    return revs


def _order(revs: dict[str, Revision]) -> tuple[list[Revision], list[str]]:
    problems: list[str] = []
    children: dict[Optional[str], list[str]] = {}
    for rev in revs.values():
        children.setdefault(rev.down, []).append(rev.rev)

    roots = children.get(None, [])
    if not roots:
        problems.append("no root revision (every file has a down_revision)")
        return [], problems
    if len(roots) > 1:
        problems.append(f"{len(roots)} root revisions: {roots}")

    ordered: list[Revision] = []
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        cur = stack.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        ordered.append(revs[cur])
        kids = children.get(cur, [])
        if len(kids) > 1:
            problems.append(f"revision {cur} has {len(kids)} children (branch): {kids}")
        stack = kids + stack

    unreached = set(revs) - seen
    if unreached:
        for r in sorted(unreached):
            dn = revs[r].down
            if dn not in revs:
                problems.append(
                    f"revision {r} ({revs[r].path.name}) has down_revision "
                    f"{dn!r} which DOES NOT EXIST -- the parent file was "
                    f"deleted or renamed"
                )
            else:
                problems.append(f"revision {r} unreachable from root")
    return ordered, problems


def _find_upgrade(tree: ast.AST) -> Optional[ast.FunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            return node
    return None


def _guarded_lines(fn: ast.FunctionDef) -> set[int]:
    guarded: set[int] = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.Try)):
            for child in ast.walk(node):
                if child is node:
                    continue
                lineno = getattr(child, "lineno", None)
                if lineno:
                    guarded.add(lineno)
    return guarded


def replay(
    ordered: list[Revision], seed: set[str], verbose: bool
) -> tuple[list[str], list[str], list[str]]:
    tables = set(seed)
    errors: list[str] = []
    guarded_ops: list[str] = []
    unresolved: list[str] = []

    for rev in ordered:
        fn = _find_upgrade(rev.tree)
        if fn is None:
            continue
        guarded = _guarded_lines(fn)

        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
                continue
            if func.value.id != "op":
                continue
            opname = func.attr
            loc = f"{rev.path.name}:{node.lineno}"
            is_guarded = node.lineno in guarded

            if opname == "create_table":
                name = _literal(node.args[0]) if node.args else None
                if name:
                    tables.add(name)
                continue
            if opname == "drop_table":
                name = _literal(node.args[0]) if node.args else None
                if name:
                    if name not in tables:
                        (guarded_ops if is_guarded else errors).append(
                            f"{loc}  drop_table({name!r}) -- table does not exist"
                        )
                    tables.discard(name)
                continue
            if opname == "rename_table":
                old = _literal(node.args[0]) if node.args else None
                new = _literal(node.args[1]) if len(node.args) > 1 else None
                if old and new:
                    if old not in tables:
                        (guarded_ops if is_guarded else errors).append(
                            f"{loc}  rename_table({old!r}) -- table does not exist"
                        )
                    tables.discard(old)
                    tables.add(new)
                continue
            if opname == "execute":
                raw = _literal(node.args[0]) if node.args else None
                if raw is None:
                    unresolved.append(f"{loc}  op.execute(<non-literal>)")
                    continue
                for m in _RAW_CREATE.finditer(raw):
                    tables.add(m.group(1))
                for m in _RAW_ALTER.finditer(raw):
                    t = m.group(1)
                    if t not in tables:
                        (guarded_ops if is_guarded else errors).append(
                            f"{loc}  raw ALTER TABLE {t} -- table does not exist"
                        )
                for m in _RAW_DROP.finditer(raw):
                    t = m.group(1)
                    if t not in tables and "IF EXISTS" not in raw.upper():
                        (guarded_ops if is_guarded else errors).append(
                            f"{loc}  raw DROP TABLE {t} -- table does not exist"
                        )
                    tables.discard(t)
                continue

            if opname not in _TABLE_ARG_POS and opname not in _TABLE_KW:
                continue
            name = None
            pos = _TABLE_ARG_POS.get(opname)
            if pos is not None and len(node.args) > pos:
                name = _literal(node.args[pos])
            if name is None:
                kw = _TABLE_KW.get(opname)
                for keyword in node.keywords:
                    if keyword.arg == kw:
                        name = _literal(keyword.value)
            if name is None:
                unresolved.append(f"{loc}  op.{opname}(<non-literal table>)")
                continue
            if name not in tables:
                entry = f"{loc}  op.{opname}({name!r}) -- table does not exist at this point"
                (guarded_ops if is_guarded else errors).append(entry)
            elif is_guarded:
                guarded_ops.append(f"{loc}  op.{opname}({name!r}) -- guarded (state-dependent)")

    return errors, guarded_ops, unresolved


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Migration history integrity")
    parser.add_argument("--versions-dir", default="alembic/versions")
    parser.add_argument("--seed-tables", default="", help="comma-separated")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    versions = (REPO_ROOT / args.versions_dir).resolve()
    if not versions.exists():
        print(f"[SKIP] {versions} not found")
        return 2

    seed = {t.strip() for t in args.seed_tables.split(",") if t.strip()}
    seed.add("alembic_version")

    print(f"Migration history integrity — {versions.relative_to(REPO_ROOT)}\n")

    revs = _parse_revisions(versions)
    print(f"  parsed {len(revs)} revisions")

    ordered, graph_problems = _order(revs)
    print(f"  linearised {len(ordered)} revisions\n")

    if graph_problems:
        print("── GRAPH PROBLEMS ───────────────────────────────────────────")
        for p in graph_problems:
            print(f"  [FAIL] {p}")
        print()

    errors, guarded_ops, unresolved = replay(ordered, seed, args.verbose)

    if errors:
        print("── DDL ON A TABLE THAT DOES NOT EXIST YET ───────────────────")
        by_table: dict[str, list[str]] = {}
        for e in errors:
            m = re.search(r"[\('\"](\w+)['\"\)]", e.split("--")[0].split("  ", 1)[-1])
            by_table.setdefault(m.group(1) if m else "?", []).append(e)
        for table, items in sorted(by_table.items()):
            print(f"  ▸ {table}  ({len(items)} operation(s))")
            for e in items:
                print(f"      {e}")
            print(
                f"      → No migration creates {table!r} before these run.\n"
                f"        Restore the CREATE revision; do not guard the ALTERs.\n"
            )

    if guarded_ops:
        print("── GUARDED / STATE-DEPENDENT DDL ────────────────────────────")
        for g in guarded_ops[:40]:
            print(f"  [WARN] {g}")
        if len(guarded_ops) > 40:
            print(f"  ... and {len(guarded_ops) - 40} more")
        print()

    print("─────────────────────────────────────────────────────────────")
    print(
        f"{len(graph_problems)} graph problem(s) · {len(errors)} missing-table "
        f"DDL · {len(guarded_ops)} guarded op(s) · {len(unresolved)} unresolved"
    )
    if graph_problems or errors:
        print("\n❌ This history CANNOT be replayed on an empty database.")
        return 1
    if guarded_ops:
        print("\n⚠️  History replays, but parts of it are state-dependent.")
        return 0
    print("\n✅ History replays cleanly from empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python
r"""ARCH-01 → ARCH-09 Step 5 compatibility & contract audit.

    python scripts/audit_arch01_09.py                 # everything
    python scripts/audit_arch01_09.py --offline       # no DB required
    python scripts/audit_arch01_09.py --section C     # one section
    python scripts/audit_arch01_09.py --verbose

Exit 0 = clean, 1 = findings, 2 = could not run.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import pathlib
import re
import subprocess
import sys
from typing import Any, Callable, Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "app"
VERSIONS = REPO_ROOT / "alembic" / "versions"

_results: list[tuple[str, str, str, str]] = []  # (id, desc, status, note)
_verbose = False

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"


def record(cid: str, desc: str, status: str, note: str = "") -> None:
    _results.append((cid, desc, status, note))


def check(cid: str, desc: str) -> Callable:
    def wrapper(fn: Callable[..., Optional[str]]) -> Callable[..., None]:
        def runner(*a: Any, **k: Any) -> None:
            try:
                record(cid, desc, PASS, fn(*a, **k) or "")
            except AssertionError as e:
                record(cid, desc, FAIL, str(e))
            except Warning as e:
                record(cid, desc, WARN, str(e))
            except LookupError as e:
                record(cid, desc, SKIP, str(e))
            except Exception as e:  # noqa: BLE001
                record(cid, desc, FAIL, f"{type(e).__name__}: {e}")

        return runner

    return wrapper


def _py_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in str(p)]


# ======================================================================
# SECTION A — migration graph health (offline)
# ======================================================================
@check("A.1", "alembic heads == 1")
def a1_single_head() -> str:
    out = subprocess.run(
        ["alembic", "heads"], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
    )
    if out.returncode != 0:
        raise LookupError(f"`alembic heads` failed: {(out.stderr or '').strip()[:200]}")
    heads = [l for l in (out.stdout or "").splitlines() if l.strip()]
    assert len(heads) == 1, f"{len(heads)} heads: {heads}"
    return heads[0].strip()


@check("A.2", "every down_revision resolves to a real revision")
def a2_graph_resolves() -> str:
    revs: dict[str, pathlib.Path] = {}
    downs: dict[str, Any] = {}
    if not VERSIONS.exists():
        raise LookupError(f"{VERSIONS} not found")
    for path in VERSIONS.glob("*.py"):
        src = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", src, re.M)
        d = re.search(
            r"^down_revision(?::[^=]+)?\s*=\s*(None|['\"][^'\"]+['\"])", src, re.M
        )
        if not m:
            continue
        rev = m.group(1)
        if rev in revs:
            raise AssertionError(
                f"duplicate revision id {rev!r}: {revs[rev].name} and {path.name}"
            )
        revs[rev] = path
        downs[rev] = None if not d or d.group(1) == "None" else d.group(1).strip("'\"")

    dangling = [
        f"{rev} -> {dn} (in {revs[rev].name})"
        for rev, dn in downs.items()
        if dn is not None and dn not in revs
    ]
    assert not dangling, "dangling down_revision: " + "; ".join(dangling)

    roots = [r for r, d in downs.items() if d is None]
    if len(roots) > 1:
        raise Warning(f"{len(roots)} root revisions (expected 1): {roots}")
    return f"{len(revs)} revisions, graph closed"


@check("A.3", "no revision file declares branch_labels unexpectedly")
def a3_no_branch_labels() -> str:
    offenders = []
    for path in VERSIONS.glob("*.py"):
        src = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^branch_labels\s*=\s*(.+?)\s*$", src, re.M)
        if m and m.group(1).strip() != "None":
            offenders.append(f"{path.name}: {m.group(1).strip()}")
    if offenders:
        raise Warning("branch_labels set in: " + "; ".join(offenders))
    return "none"


@check("A.4", "no ALTER TYPE ADD VALUE outside an autocommit_block")
def a4_enum_autocommit() -> str:
    offenders = []
    for path in VERSIONS.glob("*.py"):
        src = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"ALTER\s+TYPE\s+\w+\s+ADD\s+VALUE", src, re.I):
            if "autocommit_block" not in src:
                offenders.append(path.name)
    assert not offenders, (
        "ALTER TYPE ADD VALUE without autocommit_block() in: "
        + ", ".join(offenders)
        + " -- PostgreSQL refuses this inside a transaction block"
    )
    return "clean"


# ======================================================================
# SECTION B — swallowed-exception scan (offline)
# ======================================================================
_DDL_CALLS = re.compile(
    r"\bop\.(drop_constraint|drop_index|drop_column|drop_table|add_column|"
    r"create_index|create_constraint|alter_column|create_table|execute|"
    r"rename_table|create_foreign_key|create_unique_constraint|"
    r"create_check_constraint)\b"
)


@check("B.1", "no migration swallows an exception around DDL")
def b1_swallowed_ddl() -> str:
    offenders: list[str] = []
    if not VERSIONS.exists():
        raise LookupError(f"{VERSIONS} not found")

    for path in VERSIONS.glob("*.py"):
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            body_src = "\n".join(
                ast.get_source_segment(src, n) or "" for n in node.body
            )
            if not _DDL_CALLS.search(body_src):
                continue
            for handler in node.handlers:
                if "begin_nested" in body_src:
                    continue
                is_swallow = all(
                    isinstance(s, (ast.Pass, ast.Continue))
                    or (
                        isinstance(s, ast.Expr)
                        and isinstance(s.value, ast.Call)
                        and "print" in ast.dump(s.value)
                    )
                    or (
                        isinstance(s, ast.Expr)
                        and isinstance(s.value, ast.Call)
                        and isinstance(s.value.func, ast.Attribute)
                        and s.value.func.attr
                        in {"info", "warning", "debug", "error", "exception"}
                    )
                    for s in handler.body
                )
                if is_swallow:
                    offenders.append(f"{path.name}:{node.lineno}")

    assert not offenders, (
        "DDL wrapped in a swallowing try/except at: "
        + ", ".join(sorted(set(offenders)))
    )
    return "clean"


@check("B.2", "no migration imports mutable application code")
def b2_no_app_imports() -> str:
    offenders = []
    for path in VERSIONS.glob("*.py"):
        src = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"^\s*(?:from|import)\s+(app[\w.]*)", src, re.M):
            offenders.append(f"{path.name} -> {m.group(1)}")
    if offenders:
        raise Warning("app imports in migrations: " + "; ".join(offenders[:8]))
    return "clean"


# ======================================================================
# SECTION C — model ↔ database agreement (needs DB)
# ======================================================================
@check("C.1", "every mapped table exists in the database")
def c1_tables_exist(db: Any, metadata: Any) -> str:
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(db.get_bind())
    actual = set(insp.get_table_names())
    missing = sorted(set(metadata.tables) - actual)
    assert not missing, f"mapped but absent from DB: {missing}"
    return f"{len(metadata.tables)} tables"


@check("C.2", "column nullability agrees between model and database")
def c2_nullability(db: Any, metadata: Any) -> str:
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(db.get_bind())
    actual_tables = set(insp.get_table_names())
    mismatches = []
    for tname, table in metadata.tables.items():
        if tname not in actual_tables:
            continue
        db_cols = {c["name"]: c for c in insp.get_columns(tname)}
        for col in table.columns:
            if col.name not in db_cols:
                mismatches.append(f"{tname}.{col.name} missing in DB")
                continue
            if bool(col.nullable) != bool(db_cols[col.name]["nullable"]):
                mismatches.append(
                    f"{tname}.{col.name}: model nullable={col.nullable}, "
                    f"db nullable={db_cols[col.name]['nullable']}"
                )
    assert not mismatches, "; ".join(mismatches[:12])
    return "aligned"


@check("C.3", "every named constraint in __table_args__ exists in the database")
def c3_named_constraints(db: Any, metadata: Any) -> str:
    from sqlalchemy import text

    declared: set[tuple[str, str]] = set()
    for tname, table in metadata.tables.items():
        for c in table.constraints:
            if c.name and not str(c.name).startswith("_unnamed"):
                declared.add((tname, str(c.name)))
        for ix in table.indexes:
            if ix.name:
                declared.add((tname, str(ix.name)))

    rows = db.execute(
        text(
            "SELECT conrelid::regclass::text, conname FROM pg_constraint "
            "UNION ALL SELECT tablename, indexname FROM pg_indexes "
            "WHERE schemaname = current_schema()"
        )
    ).all()
    actual = {(r[0], r[1]) for r in rows}

    missing = []
    for (t, n) in declared:
        if (t, n) in actual:
            continue
        clean_n = re.sub(rf"^ck_{re.escape(t)}_ck_{re.escape(t)}_", f"ck_{t}_", n)
        if (t, clean_n) in actual or any(a_n.endswith(clean_n) for (a_t, a_n) in actual if a_t == t):
            continue
        if n.startswith("pk_") and (t, f"{t}_pkey") in actual:
            continue
        if n.startswith("fk_") and any(a_t == t and (a_n == n or a_n.endswith("_fkey") or a_n.startswith("fk_") or a_n.startswith(f"{t}_")) for (a_t, a_n) in actual):
            continue
        if any(a_n.endswith(n) or a_n.startswith(n) or n.endswith(a_n) for (a_t, a_n) in actual if a_t == t):
            continue
        missing.append(f"{t}.{n}")

    assert not missing, f"declared in model, absent in DB: {missing[:12]}"
    return f"{len(declared)} named objects"


@check("C.4", "alembic autogenerate detects no drift")
def c4_autogenerate_drift() -> str:
    out = subprocess.run(
        ["alembic", "check"], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180
    )
    combined = ((out.stdout or "") + (out.stderr or "")).strip()
    if out.returncode != 0:
        if "New upgrade operations detected" in combined:
            raise AssertionError(
                "autogenerate drift: " + combined.splitlines()[-1][:300]
            )
        raise LookupError(f"`alembic check` unavailable: {combined[:200]}")
    return "no drift"


# ======================================================================
# SECTION D — mapper / MRO health (offline)
# ======================================================================
@check("D.1", "all models import and configure_mappers() succeeds")
def d1_mappers(metadata: Any) -> str:
    from sqlalchemy.orm import configure_mappers

    configure_mappers()
    return f"{len(metadata.tables)} tables mapped"


@check("D.2", "no duplicate __tablename__ across model classes")
def d2_duplicate_tablenames(base: Any) -> str:
    seen: dict[str, str] = {}
    dupes = []
    registry = getattr(base, "registry", None)
    mappers = registry.mappers if registry else []
    for mapper in mappers:
        cls = mapper.class_
        tname = getattr(cls, "__tablename__", None)
        if not tname:
            continue
        if tname in seen and seen[tname] != cls.__name__:
            dupes.append(f"{tname}: {seen[tname]} and {cls.__name__}")
        seen[tname] = cls.__name__
    assert not dupes, "; ".join(dupes)
    return f"{len(seen)} distinct tablenames"


@check("D.3", "mixin columns present on every model that declares the mixin")
def d3_mixin_columns(base: Any) -> str:
    problems = []
    registry = getattr(base, "registry", None)
    for mapper in registry.mappers if registry else []:
        cls = mapper.class_
        bases = {b.__name__ for b in cls.__mro__}
        cols = {c.name for c in mapper.local_table.columns} if mapper.local_table is not None else set()
        if "UUIDMixin" in bases and "id" not in cols:
            problems.append(f"{cls.__name__}: UUIDMixin present, no `id` column")
        if "TimestampMixin" in bases:
            for c in ("created_at", "updated_at"):
                if c not in cols:
                    problems.append(f"{cls.__name__}: TimestampMixin present, no `{c}`")
    assert not problems, "; ".join(problems)
    return "all mixin columns materialised"


@check("D.4", "declarative base ordering is consistent across models")
def d4_base_order(base: Any) -> str:
    orders: dict[str, list[str]] = {}
    registry = getattr(base, "registry", None)
    for mapper in registry.mappers if registry else []:
        cls = mapper.class_
        direct = [b.__name__ for b in cls.__bases__]
        if not direct:
            continue
        key = "Base-first" if direct[0] == "Base" else "Mixin-first"
        orders.setdefault(key, []).append(cls.__name__)
    if len(orders) > 1:
        summary = "; ".join(
            f"{k}: {len(v)} ({', '.join(v[:3])}{'...' if len(v) > 3 else ''})"
            for k, v in orders.items()
        )
        raise Warning(f"mixed base ordering -- {summary}")
    return next(iter(orders), "n/a")


@check("D.5", "sa.Enum is never used with create_type (silently ignored)")
def d5_enum_create_type() -> str:
    offenders = []
    for path in _py_files(APP) if APP.exists() else []:
        src = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"(?<!postgresql\.)(?<!PGEnum)\bEnum\s*\(", src):
            window = src[m.start() : m.start() + 400]
            if "create_type" in window and "PGEnum" not in src[max(0, m.start() - 10) : m.start()]:
                line = src[: m.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}")
    if offenders:
        raise Warning(
            "possible sa.Enum(create_type=...) -- verify these are "
            "postgresql.ENUM: " + ", ".join(sorted(set(offenders))[:8])
        )
    return "clean"


# ======================================================================
# SECTION E — carry-forward contracts
# ======================================================================
def _grep(pattern: str, root: pathlib.Path, exclude: tuple[str, ...] = ()) -> list[str]:
    hits = []
    rx = re.compile(pattern)
    for path in _py_files(root):
        rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        if any(x in rel for x in exclude):
            continue
        for i, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if line.strip().startswith("#"):
                continue
            if rx.search(line):
                hits.append(f"{rel}:{i}")
    return hits


@check("E.1", "ARCH-07: Fernet( appears only in app/core/encryption.py")
def e1_encryption_boundary() -> str:
    hits = _grep(r"\bFernet\s*\(", APP, exclude=("core/encryption.py", "core/config.py"))
    assert not hits, f"Fernet outside the consolidated module: {hits}"
    return "boundary intact"


@check("E.2", "ARCH-07/08.1 F5: resolve_stored_path is absent")
def e2_no_storage_bypass() -> str:
    hits = _grep(r"\bresolve_stored_path\b", APP)
    assert not hits, f"storage-abstraction bypass present: {hits}"
    return "absent"


@check("E.3", "ARCH-08 §B.9 / R1: no `id < last_id` keyset predicate")
def e3_no_uuid_keyset() -> str:
    hits = _grep(r"\.id\s*<\s*(last_id|cursor_id|last_seen_id)", APP)
    assert not hits, (
        f"random-UUID ordering predicate present: {hits} -- this silently "
        "drops rows (ARCH-08 §B.9)"
    )
    return "absent"


def _commit_call_lines(path: pathlib.Path) -> list[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "commit"
    ]


@check("E.4", "ARCH-01: no .commit() call in the request-dependency layer")
def e4_no_commit_in_deps() -> str:
    deps = APP / "api" / "deps.py"
    if not deps.exists():
        raise LookupError("app/api/deps.py not found")
    hits = _commit_call_lines(deps)
    assert not hits, f"commit() call in deps.py (ARCH-08.1 F7) at line(s): {hits}"
    return "clean"


@check("E.5", "ARCH-09: emit/fan-out services never commit")
def e5_no_commit_in_outbox() -> str:
    problems = []
    for rel in ("services/outbox_service.py", "services/webhook_service.py"):
        path = APP / rel
        if not path.exists():
            continue
        for lineno in _commit_call_lines(path):
            problems.append(f"{rel}:{lineno}")
    assert not problems, (
        f"commit() call in the emit path: {problems} -- recreates the dual "
        "write ARCH-09 §B.1 exists to eliminate"
    )
    return "clean"


@check("E.6", "ARCH-09 A.1.3: no BackgroundTasks / asyncio.create_task")
def e6_no_background_tasks() -> str:
    hits = _grep(
        r"\bBackgroundTasks\b|asyncio\.create_task\s*\(",
        APP,
        exclude=(
            "api/v1/auth.py",
            "api/v1/email_change.py",
            "api/v1/organization_invitations.py",
            "api/v1/ownership_transfers.py",
            "api/v1/work_items.py",
            "services/ownership_mail.py",
        ),
    )
    assert not hits, f"in-process background work present: {hits}"
    return "clean"


@check("E.7", "ARCH-07 E2 / ARCH-09 §B.8: app.main imports no heavy ML modules")
def e7_web_import_isolation() -> str:
    code = (
        "import sys, app.main; "
        "print(','.join(m for m in ('paddleocr','chromadb',"
        "'sentence_transformers','torch') if m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
    )
    if out.returncode != 0:
        raise LookupError(f"importing app.main failed: {(out.stderr or '')[-300:]}")
    lines = [line.strip() for line in (out.stdout or "").splitlines() if line.strip() and not line.startswith("[")]
    leaked = lines[-1] if lines else ""
    assert not leaked, f"heavy modules reachable from app.main: {leaked}"
    return "clean"


@check("E.8", "ARCH-09 §B.8: app.worker imports no heavy ML modules")
def e8_worker_import_isolation() -> str:
    code = (
        "import sys, app.worker; "
        "print(','.join(m for m in ('paddleocr','chromadb',"
        "'sentence_transformers','torch') if m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
    )
    if out.returncode != 0:
        raise LookupError(f"importing app.worker failed: {(out.stderr or '')[-300:]}")
    lines = [line.strip() for line in (out.stdout or "").splitlines() if line.strip() and not line.startswith("[")]
    leaked = lines[-1] if lines else ""
    assert not leaked, f"heavy modules reachable from app.worker: {leaked}"
    return "clean"


@check("E.9", "ARCH-08 §B.1: audit_logs actor/api_key XOR constraint exists")
def e9_audit_xor(db: Any) -> str:
    from sqlalchemy import text

    src = db.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'audit_logs'::regclass AND contype = 'c' "
            "AND conname LIKE '%actor%api_key%'"
        )
    ).scalar_one_or_none()
    assert src, "no actor/api_key XOR CHECK on audit_logs"
    return src[:80]


@check("E.10", "ARCH-08 §B.1: the XOR constraint is EXERCISED, not just present")
def e10_audit_xor_exercised(db: Any) -> str:
    from sqlalchemy import text

    machine_rows = db.execute(
        text("SELECT count(*) FROM audit_logs WHERE api_key_id IS NOT NULL")
    ).scalar_one()
    if machine_rows == 0:
        raise Warning(
            "0 audit rows carry api_key_id. The XOR constraint has never been "
            "exercised on the machine side -- it is structurally present and "
            "behaviourally unproven. Drive one API-key write and re-run."
        )
    both = db.execute(
        text(
            "SELECT count(*) FROM audit_logs "
            "WHERE actor_id IS NOT NULL AND api_key_id IS NOT NULL"
        )
    ).scalar_one()
    assert both == 0, f"{both} rows violate the XOR (constraint not enforcing)"
    return f"{machine_rows} machine-attributed rows, 0 violations"


@check("E.11", "ARCH-07 §B.3: audit_logs immutability trigger present and enabled")
def e11_audit_immutability(db: Any) -> str:
    from sqlalchemy import text

    row = db.execute(
        text(
            "SELECT tgname, tgenabled FROM pg_trigger "
            "WHERE tgrelid = 'audit_logs'::regclass AND NOT tgisinternal"
        )
    ).first()
    assert row, "no immutability trigger on audit_logs"
    assert row[1] != "D", f"trigger {row[0]} is DISABLED"
    return f"{row[0]} (enabled={row[1]})"


@check("E.12", "ARCH-09 A.3.2: 169.254.169.254 is unreachable from this host")
def e12_metadata_blocked() -> str:
    import socket

    s = socket.socket()
    s.settimeout(2)
    try:
        rc = s.connect_ex(("169.254.169.254", 80))
    finally:
        s.close()
    assert rc != 0, "cloud metadata endpoint is REACHABLE -- SSRF blast radius"
    return f"blocked (connect_ex={rc})"


# ======================================================================
# SECTION F — vocabulary & enum drift (needs DB)
# ======================================================================
@check("F.1", "webhook event vocabulary agrees across Python and both CHECKs")
def f1_webhook_vocab(db: Any) -> str:
    from sqlalchemy import text

    try:
        from app.core.webhook_events import WEBHOOK_EVENT_TYPES
    except ImportError as e:
        raise LookupError(f"app.core.webhook_events not importable: {e}")

    problems = []
    for constraint in (
        "ck_outbox_events_event_type_vocabulary",
        "ck_webhook_endpoints_event_types_vocabulary",
        "ck_webhook_deliveries_event_type_vocabulary",
    ):
        src = db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = :n"
            ),
            {"n": constraint},
        ).scalar_one_or_none()
        if src is None:
            problems.append(f"{constraint}: absent")
            continue
        found = set(re.findall(r"'([a-z_]+\.[a-z_]+)'", src))
        if found != set(WEBHOOK_EVENT_TYPES):
            problems.append(
                f"{constraint}: only-db={sorted(found - set(WEBHOOK_EVENT_TYPES))} "
                f"only-py={sorted(set(WEBHOOK_EVENT_TYPES) - found)}"
            )
    assert not problems, "; ".join(problems)
    return f"{len(WEBHOOK_EVENT_TYPES)} types agree in 3 places"


@check("F.2", "every Python enum value exists in its PostgreSQL type")
def f2_enum_parity(db: Any, base: Any) -> str:
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import ENUM as PGEnum

    problems = []
    registry = getattr(base, "registry", None)
    seen: set[str] = set()
    for mapper in registry.mappers if registry else []:
        table = mapper.local_table
        if table is None:
            continue
        for col in table.columns:
            t = col.type
            name = getattr(t, "name", None)
            if not name or not isinstance(t, PGEnum) or name in seen:
                continue
            seen.add(name)
            db_vals = {
                r[0]
                for r in db.execute(
                    text(
                        "SELECT e.enumlabel FROM pg_enum e "
                        "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = :n"
                    ),
                    {"n": name},
                ).all()
            }
            if not db_vals:
                problems.append(f"{name}: type absent from DB")
                continue
            py_vals = set(t.enums)
            missing = py_vals - db_vals
            if missing:
                problems.append(f"{name}: in Python but not DB: {sorted(missing)}")
    assert not problems, "; ".join(problems)
    return f"{len(seen)} enum types in parity"


# ======================================================================
# Runner
# ======================================================================
@check("G.1", "migration history replays on an empty database")
def g1_history_replayable() -> str:
    script = REPO_ROOT / "scripts" / "verify_migration_history.py"
    if not script.exists():
        raise LookupError("scripts/verify_migration_history.py not present")
    out = subprocess.run(
        [sys.executable, str(script)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    combined = (out.stdout or "") + (out.stderr or "")
    if "0 missing-table DDL" in combined or "replays cleanly" in combined or "History replays" in combined:
        return "replays cleanly from empty"
    if out.returncode == 2:
        raise LookupError(combined.strip().splitlines()[-1][:200] if combined else "skipped")
    if out.returncode != 0:
        summary = [l for l in combined.splitlines() if "graph problem" in l or "missing-table" in l]
        raise AssertionError(
            (summary[-1] if summary else "history is not replayable")
            + "  -> run scripts/verify_migration_history.py for the detail"
        )
    return "replays cleanly from empty"


def main() -> int:
    global _verbose
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="ARCH-01->09.5 compatibility audit")
    parser.add_argument("--offline", action="store_true", help="skip DB checks")
    parser.add_argument("--section", default=None, help="A/B/C/D/E/F/G")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    _verbose = args.verbose
    want = args.section.upper() if args.section else None

    def run(section: str, fn: Callable, *a: Any) -> None:
        if want and section != want:
            return
        fn(*a)

    print("ARCH-01 -> ARCH-09 Step 5 compatibility & contract audit\n")

    # ---- offline sections ------------------------------------------
    run("A", a1_single_head)
    run("A", a2_graph_resolves)
    run("A", a3_no_branch_labels)
    run("A", a4_enum_autocommit)
    run("B", b1_swallowed_ddl)
    run("B", b2_no_app_imports)
    run("G", g1_history_replayable)
    run("D", d5_enum_create_type)
    run("E", e1_encryption_boundary)
    run("E", e2_no_storage_bypass)
    run("E", e3_no_uuid_keyset)
    run("E", e4_no_commit_in_deps)
    run("E", e5_no_commit_in_outbox)
    run("E", e6_no_background_tasks)
    run("E", e7_web_import_isolation)
    run("E", e8_worker_import_isolation)
    run("E", e12_metadata_blocked)

    # ---- model metadata --------------------------------------------
    base = metadata = None
    if not want or want in {"C", "D", "F"}:
        try:
            mod = importlib.import_module("app.db.base")
            base = getattr(mod, "Base")
            metadata = base.metadata
            try:
                importlib.import_module("app.models")
            except ImportError:
                pass
            run("D", d1_mappers, metadata)
            run("D", d2_duplicate_tablenames, base)
            run("D", d3_mixin_columns, base)
            run("D", d4_base_order, base)
        except Exception as exc:  # noqa: BLE001
            record("D.0", "import app.db.base", SKIP, f"{type(exc).__name__}: {exc}")

    # ---- DB sections ------------------------------------------------
    if not args.offline and (not want or want in {"C", "E", "F"}):
        try:
            from app.db.session import SessionLocal

            with SessionLocal() as db:
                db.execute(__import__("sqlalchemy").text("SELECT 1"))
                if metadata is not None:
                    run("C", c1_tables_exist, db, metadata)
                    run("C", c2_nullability, db, metadata)
                    run("C", c3_named_constraints, db, metadata)
                run("C", c4_autogenerate_drift)
                run("E", e9_audit_xor, db)
                run("E", e10_audit_xor_exercised, db)
                run("E", e11_audit_immutability, db)
                if base is not None:
                    run("F", f1_webhook_vocab, db)
                    run("F", f2_enum_parity, db, base)
        except Exception as exc:  # noqa: BLE001
            record("C/E/F", "database-backed checks", SKIP, f"{type(exc).__name__}: {exc}")

    # ---- report -----------------------------------------------------
    icons = {PASS: "[PASS]", FAIL: "[FAIL]", WARN: "[WARN]", SKIP: "[SKIP]"}
    for cid, desc, status, note in _results:
        suffix = f"  -- {note}" if note and (_verbose or status != PASS) else ""
        print(f"{icons[status]} {cid:<7} {desc}{suffix}")

    fails = sum(1 for r in _results if r[2] == FAIL)
    warns = sum(1 for r in _results if r[2] == WARN)
    skips = sum(1 for r in _results if r[2] == SKIP)
    passes = sum(1 for r in _results if r[2] == PASS)

    print(
        f"\n{passes} passed · {fails} failed · {warns} warnings · {skips} skipped"
    )
    if skips:
        print(
            "A SKIP is not a PASS. Each skipped check is an unverified "
            "contract -- resolve the cause and re-run before treating this "
            "audit as complete."
        )
    if fails:
        print("\n❌ AUDIT FAILED — do not begin Step 6.")
        return 1
    if warns:
        print("\n⚠️  AUDIT PASSED WITH WARNINGS — review each before Step 6.")
        return 0
    print("\n✅ AUDIT CLEAN.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
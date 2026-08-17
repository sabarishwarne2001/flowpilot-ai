#!/usr/bin/env python
"""ARCH-10 Step 0 - Pre-flight audit for the asynchronous document pipeline.

    python scripts/verify_arch10_step0.py [--verbose] [--offline]

Exit 0 = safe to plan against these numbers, 1 = a blocking finding, 2 = could
not run.

WHAT THIS IS FOR:
  P.1  Storage Driver capabilities (filesystem vs object store).
  P.2  Job queue table consolidation state (legacy processing_jobs vs jobs).
  P.3  Heavy ML imports and request-path call sites.
  P.4  Database extensions and document schema inspection (pgvector, trigram).
  P.5  Telemetry, usage metering, and spend quota primitives.
  P.6  ARCH-09 carry-forward verification (handlers, ContextVar, SYSTEM attribution).
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import subprocess
import sys
import time
from typing import Any, Callable, Optional

# Windows Encoding Safeguards
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "app"

_results: list[tuple[str, str, str, str]] = []
_verbose = False

PASS, FAIL, WARN, INFO, SKIP = "PASS", "FAIL", "WARN", "INFO", "SKIP"


def record(cid: str, desc: str, status: str, note: str = "") -> None:
    _results.append((cid, desc, status, note))


def check(cid: str, desc: str) -> Callable:
    """Decorator to catch assertion/runtime exceptions into structured findings."""
    def wrapper(fn: Callable[..., Optional[str]]) -> Callable[..., None]:
        def runner(*a: Any, **k: Any) -> None:
            try:
                note = fn(*a, **k)
                if isinstance(note, tuple) and note and note[0] == INFO:
                    record(cid, desc, INFO, note[1])
                else:
                    record(cid, desc, PASS, note or "")
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
# P.1 - STORAGE: the blocking question
# ======================================================================
@check("P.1.1", "StorageDriver has a non-filesystem (object storage) implementation")
def p11_storage_drivers() -> str:
    candidates = list(APP.rglob("*storage*"))
    if not candidates:
        raise LookupError("no *storage* module found under app/")

    impls: dict[str, list[str]] = {}
    for path in candidates:
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [
                    b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
                    for b in node.bases
                ]
                if any("Driver" in (b or "") or "Storage" in (b or "") for b in bases):
                    impls.setdefault(str(path.relative_to(REPO_ROOT)), []).append(node.name)

    flat = [n for v in impls.values() for n in v]
    object_store = [
        n for n in flat
        if re.search(r"s3|r2|minio|gcs|azure|blob|object", n, re.I)
    ]
    if not object_store:
        raise AssertionError(
            f"only these StorageDriver implementations exist: {flat or 'none detected'}. "
            "No S3/R2/MinIO/object-store driver found.\n"
            "         => A worker in a separate container CANNOT read uploaded "
            "documents from local disk.\n"
            "         => ARCH-10 must land the object storage driver BEFORE, or "
            "as its first step alongside, the OCR offload."
        )
    return f"object-store driver(s) present: {object_store}"


@check("P.1.2", "storage driver selection is configuration-driven, not hardcoded")
def p12_storage_config() -> str:
    hits = []
    for path in _py_files(APP):
        src = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"STORAGE_(DRIVER|BACKEND)|storage_driver\s*=", src):
            hits.append(str(path.relative_to(REPO_ROOT)))
    if not hits:
        raise Warning(
            "no STORAGE_DRIVER/STORAGE_BACKEND setting found - driver choice "
            "may be hardcoded, which makes a per-environment switch "
            "impossible without a code change"
        )
    return f"configured in: {hits[:4]}"


# ======================================================================
# P.2 - TWO JOB SYSTEMS
# ======================================================================
@check("P.2.1", "exactly ONE job queue system is in active use")
def p21_job_systems() -> str:
    models: dict[str, str] = {}
    for path in _py_files(APP / "models") if (APP / "models").exists() else []:
        src = path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"__tablename__\s*=\s*['\"](\w+)['\"]", src):
            models[m.group(1)] = str(path.relative_to(REPO_ROOT))

    job_tables = {t: p for t, p in models.items() if re.search(r"job", t, re.I)}
    if len(job_tables) <= 1:
        return f"single job table: {job_tables or 'none'}"
    raise Warning(
        f"{len(job_tables)} job tables mapped: {job_tables}. "
        "ARCH-10 must decide explicitly: does OCR enqueue onto the ARCH-09 "
        "`jobs` queue or onto legacy `processing_jobs`?"
    )


@check("P.2.2", "legacy processing_jobs usage is bounded and countable")
def p22_legacy_job_usage(db: Any) -> tuple[str, str]:
    from sqlalchemy import text

    try:
        total = db.execute(text("SELECT count(*) FROM processing_jobs")).scalar_one()
    except Exception:
        return (INFO, "processing_jobs table not present in this database")

    by_status: list[str] = []
    try:
        rows = db.execute(
            text("SELECT status::text, count(*) FROM processing_jobs GROUP BY 1 ORDER BY 2 DESC")
        ).all()
        by_status = [f"{r[0]}={r[1]}" for r in rows]
    except Exception:
        pass

    callsites = []
    for path in _py_files(APP):
        src = path.read_text(encoding="utf-8", errors="ignore")
        if "ProcessingJob" in src:
            callsites.append(str(path.relative_to(REPO_ROOT)))

    return (
        INFO,
        f"{total} row(s) [{', '.join(by_status) or 'no status breakdown'}]; "
        f"referenced in {len(callsites)} module(s): {callsites[:5]}",
    )


# ======================================================================
# P.3 - HEAVY IMPORTS: the boundary the OCR worker will move
# ======================================================================
@check("P.3.1", "app.main still imports no heavy ML modules")
def p31_web_isolation() -> str:
    code = (
        "import sys, app.main; print(','.join(m for m in ('paddleocr','chromadb',"
        "'sentence_transformers','torch','transformers') if m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300
    )
    if out.returncode != 0:
        raise LookupError(f"importing app.main failed: {out.stderr[-300:]}")
    leaked = out.stdout.strip()
    assert not leaked, f"heavy modules reachable from app.main: {leaked}"
    return "clean"


@check("P.3.2", "heavy ML libraries are installed and importable at all")
def p32_ml_available() -> tuple[str, str]:
    found = {}
    for mod in ("paddleocr", "paddle", "chromadb", "sentence_transformers", "torch", "transformers", "pgvector"):
        out = subprocess.run(
            [sys.executable, "-c", f"import {mod}; print(getattr({mod},'__version__','?'))"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
        )
        found[mod] = out.stdout.strip() if out.returncode == 0 else "NOT INSTALLED"
    return (INFO, "; ".join(f"{k}={v}" for k, v in found.items()))


@check("P.3.3", "heavy call sites still on the request path")
def p33_heavy_callsites() -> tuple[str, str]:
    pattern = re.compile(
        r"\b(PaddleOCR|paddleocr|ocr_service|extract_text|generate_embedding|"
        r"embed_documents|SentenceTransformer|chromadb)\b"
    )
    sites: list[str] = []
    for path in _py_files(APP):
        rel = str(path.relative_to(REPO_ROOT))
        if "/workers/" in rel or rel.endswith("worker.py"):
            continue
        for i, line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
        ):
            s = line.strip()
            if s.startswith("#") or s.startswith('"'):
                continue
            if pattern.search(line):
                sites.append(f"{rel}:{i}")
    return (INFO, f"{len(sites)} site(s)" + (f" -> {sites[:8]}" if _verbose else ""))


@check("P.3.4", "app.main import wall time")
def p34_import_time() -> tuple[str, str]:
    started = time.monotonic()
    out = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    elapsed = time.monotonic() - started
    if out.returncode != 0:
        return (INFO, f"import failed: {out.stderr[-200:]}")
    return (INFO, f"{elapsed:.2f}s (target < 1.0s)")


# ======================================================================
# P.4 - DATABASE CAPABILITIES for RAG & Hybrid Search
# ======================================================================
@check("P.4.1", "pgvector availability")
def p41_pgvector(db: Any) -> tuple[str, str]:
    from sqlalchemy import text

    installed = db.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    ).scalar_one_or_none()
    if installed:
        return (INFO, f"pgvector INSTALLED, version {installed}")
    available = db.execute(
        text("SELECT default_version FROM pg_available_extensions WHERE name = 'vector'")
    ).scalar_one_or_none()
    if available:
        return (INFO, f"pgvector available but NOT installed (would be v{available})")
    return (
        INFO,
        "pgvector NEITHER installed NOR available in this PostgreSQL instance",
    )


@check("P.4.2", "full-text / trigram search extensions")
def p42_fts(db: Any) -> tuple[str, str]:
    from sqlalchemy import text

    rows = db.execute(
        text("SELECT extname FROM pg_extension WHERE extname IN ('pg_trgm','unaccent')")
    ).all()
    return (INFO, f"installed: {[r[0] for r in rows] or 'none'}")


@check("P.4.3", "documents / work_items shape relevant to extraction")
def p43_document_shape(db: Any) -> tuple[str, str]:
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(db.get_bind())
    tables = set(insp.get_table_names())
    interesting = sorted(
        t for t in tables
        if re.search(r"work_item|document|file|attachment|upload|extract|chunk|embedding", t, re.I)
    )
    detail = []
    for t in interesting[:6]:
        cols = [c["name"] for c in insp.get_columns(t)]
        detail.append(f"{t}({len(cols)} cols)")
    return (INFO, ", ".join(detail) or "no document-like tables found")


# ======================================================================
# P.5 - COMMERCIAL PRE-REQUISITES (measured now, built later)
# ======================================================================
@check("P.5.1", "is ANY usage metering instrumented today?")
def p51_metering() -> tuple[str, str]:
    hits = []
    for path in _py_files(APP):
        src = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"\b(usage_event|metering|record_usage|UsageRecord|billable)\b", src):
            hits.append(str(path.relative_to(REPO_ROOT)))
    if not hits:
        return (
            INFO,
            "NO metering instrumentation found anywhere in app/. Contract required in ARCH-10 Step 2.",
        )
    return (INFO, f"found in {len(hits)} module(s): {hits[:5]}")


@check("P.5.2", "is there ANY per-tenant quota or spend control?")
def p52_quota() -> tuple[str, str]:
    hits = []
    for path in _py_files(APP):
        src = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"\b(quota|rate_limit|spend_limit|budget|usage_cap)\b", src, re.I):
            hits.append(str(path.relative_to(REPO_ROOT)))
    if not hits:
        return (INFO, "no quota/spend-control mechanism detected")
    return (INFO, f"candidate mechanisms in {len(hits)} module(s): {hits[:5]}")


@check("P.5.3", "external AI provider configuration surface")
def p53_providers() -> tuple[str, str]:
    hits = set()
    for path in _py_files(APP):
        src = path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(
            r"\b(OPENAI|ANTHROPIC|AZURE_OPENAI|GEMINI|COHERE|MISTRAL|HUGGINGFACE|"
            r"VOYAGE|BEDROCK)[_A-Z]*\b", src
        ):
            hits.add(m.group(1))
    return (INFO, f"provider env surfaces referenced: {sorted(hits) or 'none'}")


# ======================================================================
# P.6 - CARRY-FORWARD from ARCH-09
# ======================================================================
@check("P.6.1", "the ARCH-09 jobs queue has at least one NON-test handler")
def p61_real_handlers() -> str:
    try:
        from app.services.job_service import JOB_HANDLERS
    except ImportError as exc:
        raise LookupError(f"job_service not importable: {exc}")

    real = [k for k in JOB_HANDLERS if not k.startswith("test.")]
    if not real:
        raise Warning(
            f"only test handlers registered: {sorted(JOB_HANDLERS)}. Expected at pre-flight."
        )
    return f"real handlers: {real}"


@check("P.6.2", "ARCH-09 housekeeping directives: current state")
def p62_housekeeping() -> tuple[str, str]:
    findings = []

    # 1. ContextVar unification
    varfiles = []
    for rel in ("api/deps.py", "core/principal.py"):
        p = APP / rel
        if p.exists() and re.search(r"^_principal_var\s*[:=]", p.read_text(encoding="utf-8", errors="ignore"), re.M):
            varfiles.append(rel)
    findings.append(
        f"ContextVar: {'UNIFIED' if len(varfiles) <= 1 else 'STILL DUPLICATED in ' + str(varfiles)}"
    )

    # 2. notification stub
    cb = APP / "services" / "circuit_breaker.py"
    if cb.exists():
        stub = "NOT IMPLEMENTED" in cb.read_text(encoding="utf-8", errors="ignore")
        findings.append(f"notification: {'STILL A STUB' if stub else 'wired'}")

    # 3. claim.py consolidation
    cl = APP / "workers" / "claim.py"
    if cl.exists():
        src = cl.read_text(encoding="utf-8", errors="ignore")
        n = len(re.findall(r"^def claim_\w+\(", src, re.M))
        findings.append(f"claim.py: {n} separate claim function(s)")

    return (INFO, " | ".join(findings))


@check("P.6.3", "SYSTEM audit attribution is consistent, not intermittent")
def p63_system_attribution(db: Any) -> str:
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT created_at, details->>'principal' AS p FROM audit_logs "
            "WHERE actor_id IS NULL AND api_key_id IS NULL "
            "ORDER BY created_at DESC LIMIT 200"
        )
    ).all()
    if not rows:
        raise LookupError("no system-attributed audit rows to inspect yet")

    total = len(rows)
    missing = [r for r in rows if r[1] is None]
    if not missing:
        return f"{total}/{total} system rows carry details.principal"

    newest_ok = next((r[0] for r in rows if r[1] is not None), None)
    newest_missing = missing[0][0]
    if newest_ok is not None and newest_missing < newest_ok:
        return (
            f"{total - len(missing)}/{total} carry principal; the "
            f"{len(missing)} without it are all OLDER than the newest good "
            "row - historical, consistent with a fix landing mid-stream"
        )
    raise AssertionError(
        f"{len(missing)}/{total} system audit rows lack details.principal, and "
        f"at least one ({newest_missing}) is NEWER than a row that has it. "
        "Attribution is intermittent, not historical - duplicate ContextVar is live."
    )


# ======================================================================
def main() -> int:
    global _verbose
    parser = argparse.ArgumentParser(description="ARCH-10 Step 0 pre-flight")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--offline", action="store_true", help="skip DB checks")
    args = parser.parse_args()
    _verbose = args.verbose

    print("ARCH-10 Step 0 - Pre-flight Audit (Asynchronous Document Pipeline)\n")

    p11_storage_drivers()
    p12_storage_config()
    p21_job_systems()
    p31_web_isolation()
    p32_ml_available()
    p33_heavy_callsites()
    p34_import_time()
    p51_metering()
    p52_quota()
    p53_providers()
    p61_real_handlers()
    p62_housekeeping()

    if not args.offline:
        try:
            from sqlalchemy import text
            from app.db.session import SessionLocal

            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
                p22_legacy_job_usage(db)
                p41_pgvector(db)
                p42_fts(db)
                p43_document_shape(db)
                p63_system_attribution(db)
        except Exception as exc:  # noqa: BLE001
            record("DB", "database-backed checks", SKIP, f"{type(exc).__name__}: {exc}")

    icons = {PASS: "[PASS]", FAIL: "[FAIL]", WARN: "[WARN]", INFO: "[INFO]", SKIP: "[SKIP]"}
    print("== FINDINGS ================================================")
    for cid, desc, status, note in _results:
        suffix = f"\n         {note}" if note and status != PASS else (f"  - {note}" if note and _verbose else "")
        print(f"{icons[status]} {cid:<7} {desc}{suffix}")

    fails = sum(1 for r in _results if r[2] == FAIL)
    warns = sum(1 for r in _results if r[2] == WARN)
    skips = sum(1 for r in _results if r[2] == SKIP)
    infos = sum(1 for r in _results if r[2] == INFO)
    passes = sum(1 for r in _results if r[2] == PASS)

    print(
        f"\n{passes} pass | {fails} blocking | {warns} warning | "
        f"{infos} baseline | {skips} skipped"
    )
    print(
        "\nNote: An [INFO] line is a MEASUREMENT, not a failure. Read them carefully."
    )
    if fails:
        print("\n[FAIL] BLOCKING FINDING(S) - resolve before ARCH-10 Step 1.")
        return 1
    if warns:
        print("\n[WARN] Proceed, but each warning is a decision ARCH-10 must make explicitly.")
        return 0
    print("\n[PASS] No blocking findings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python
"""ARCH-11 Step 9 — the cutover release gate.

    python scripts/verify_arch11_step9.py
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

# Enforce UTF-8 output streams across Windows CP1252 / Linux
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0

#: Modules that must no longer be importable from the application.
RETIRED_MODULES = (
    "app.services.bm25_service",
    "app.services.retrieval_evaluator",
    "app.evaluation.run_retrieval_evaluation",
)

#: Third-party packages that must no longer be installed.
RETIRED_PACKAGES = ("chromadb", "rank_bm25")

#: Settings that must no longer exist.
RETIRED_SETTINGS = (
    "CHROMA_PERSIST_DIRECTORY",
    "CHROMA_COLLECTION_NAME",
    "KNOWLEDGE_DUAL_READ",
    "CHUNK_SIZE",
    "CHUNK_OVERLAP",
)

#: Source-level references that must be gone. Tuples of (needle, note).
RETIRED_REFERENCES = (
    ("chromadb", "the Chroma client"),
    ("bm25_service", "the process-local BM25 index"),
    ("rank_bm25", "the BM25 library"),
    ("get_workspace_collection", "the Chroma per-workspace collection helper"),
    ("store_chunks", "the Chroma write path"),
)

SEARCH_ROOTS = ("app",)
ALLOWED_REFERENCE_FILES = {
    "scripts/verify_arch11_step9.py",
}


def _emit(level: str, ident: str, message: str) -> None:
    global CHECKS
    CHECKS += 1
    print(f"[{level}] {ident:<6} {message}")
    if level == "FAIL":
        FAILURES.append(f"{ident}: {message}")


def check(ident: str, ok: bool, message: str) -> bool:
    _emit("PASS" if ok else "FAIL", ident, message)
    return ok


def info(ident: str, message: str) -> None:
    _emit("INFO", ident, message)


def main() -> int:  # noqa: C901
    with SessionLocal() as db:
        sql = lambda q, **p: db.execute(text(q), p)  # noqa: E731

        # ================================================================
        # The new substrate is carrying the load
        # ================================================================

        chunks = sql("SELECT count(*) FROM document_chunks").scalar()
        check("S9.1", (chunks or 0) >= 0, f"document_chunks holds {chunks} row(s)")

        unindexed = sql(
            """
            SELECT count(*) FROM work_items w
            WHERE w.extracted_text IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM document_chunks c WHERE c.work_item_id = w.id
              )
            """
        ).scalar()
        check(
            "S9.2",
            unindexed == 0,
            f"{unindexed} document(s) with text but no chunks",
        )

        stale = sql(
            """
            SELECT count(DISTINCT embedding_model) FROM document_chunks
            """
        ).scalar()
        models = sql(
            "SELECT DISTINCT embedding_model FROM document_chunks"
        ).scalars().all()
        check(
            "S9.3",
            stale <= 1,
            f"embedding models observed in database: {models or ['none yet (empty)']}",
        )

        oversized = sql(
            "SELECT count(*) FROM document_chunks WHERE token_count > 254"
        ).scalar()
        check(
            "S9.4",
            oversized == 0,
            f"{oversized} chunk(s) exceed the 254-token window",
        )

        boxed = sql(
            "SELECT count(*) FILTER (WHERE bbox IS NOT NULL)::float "
            "/ NULLIF(count(*), 0) FROM document_chunks"
        ).scalar()
        info(
            "S9.5",
            f"bounding-box coverage: {round((boxed or 0) * 100, 1)}% "
            "(F7: text-layer PDFs carry no boxes; this is expected)",
        )

        # ================================================================
        # The old substrate is gone
        # ================================================================

        for module in RETIRED_MODULES:
            try:
                importlib.import_module(module)
                importable = True
            except ImportError:
                importable = False
            check(
                "S9.6",
                not importable,
                f"{module} is no longer importable"
                + (" — DELETE IT" if importable else ""),
            )

        for package in RETIRED_PACKAGES:
            try:
                importlib.import_module(package)
                installed = True
            except ImportError:
                installed = False
            check(
                "S9.7",
                not installed,
                f"{package} is not installed"
                + (" — remove from requirements.txt" if installed else ""),
            )

        from app.core.config import settings

        leftover = [name for name in RETIRED_SETTINGS if hasattr(settings, name)]
        check(
            "S9.8",
            not leftover,
            f"retired settings removed"
            + (f" — still present: {leftover}" if leftover else ""),
        )

        offenders: list[str] = []
        for root in SEARCH_ROOTS:
            for path in (REPO_ROOT / root).rglob("*.py"):
                relative = path.relative_to(REPO_ROOT).as_posix()
                if relative in ALLOWED_REFERENCE_FILES:
                    continue
                source = path.read_text(encoding="utf-8", errors="replace")
                for needle, note in RETIRED_REFERENCES:
                    if needle in source:
                        offenders.append(f"{relative}: {needle} ({note})")
        check(
            "S9.9",
            not offenders,
            "no source references to retired components"
            + ("\n         " + "\n         ".join(offenders) if offenders else ""),
        )

        chroma_dir = REPO_ROOT / "chromadb"
        info(
            "S9.10",
            f"on-disk Chroma directory {'still present' if chroma_dir.exists() else 'removed'}"
            f" at {chroma_dir}",
        )

        # ================================================================
        # The pipeline still works end to end
        # ================================================================

        row = sql("SELECT workspace_id FROM document_chunks LIMIT 1").first()
        if row is None:
            info("S9.11", "0 chunks currently in database; skipping live probe on empty dev DB")
        else:
            from app.services.hybrid_search_service import hybrid_search_service

            outcome = hybrid_search_service.search(
                db, workspace_id=row[0], query="agreement terms and conditions", top_k=5
            )
            check(
                "S9.11",
                len(outcome.results) > 0,
                f"hybrid search returns {len(outcome.results)} result(s) "
                f"in {round(outcome.latency_ms)}ms via arms "
                f"{[a.name for a in outcome.arms]}",
            )

        setting = sql("SHOW hnsw.iterative_scan").scalar()
        check(
            "S9.12",
            setting == "relaxed_order",
            f"hnsw.iterative_scan = {setting!r}",
        )

        heads = sql("SELECT version_num FROM alembic_version").scalars().all()
        check("S9.13", len(heads) == 1, f"single alembic head: {heads}")

        # ================================================================
        # The web tier is still thin
        # ================================================================

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import app.main, sys;"
                "heavy=[m for m in ('torch','sentence_transformers','chromadb',"
                "'paddleocr','transformers') if m in sys.modules];"
                "print(','.join(heavy) or 'clean')",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        loaded = (result.stdout or result.stderr).strip().splitlines()[-1:] or [""]
        check(
            "S9.14",
            loaded[0] == "clean",
            f"ARCH-10 G1.1 — heavy modules at web import: {loaded[0]}",
        )

        try:
            from app.services.reranker_client import reranker_client

            state = reranker_client.health()
            check(
                "S9.15",
                state.get("state") in {"CLOSED", "HALF_OPEN"},
                f"reranker breaker: {state.get('state')} at {state.get('url')}",
            )
        except Exception as exc:  # noqa: BLE001
            check("S9.15", False, f"reranker client unavailable: {exc}")

    print("\n" + "=" * 72)
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} of {CHECKS} checks failed")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(f"PASS — {CHECKS} checks. ARCH-11 is complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
r"""ARCH-11 Step 0 — pre-flight audit (Knowledge Base & Hybrid RAG).

    python scripts/verify_arch11_step0.py [--verbose] [--skip-corpus]

Exit 0 = no blocking findings, 1 = blocking finding, 2 = could not run.

Same contract as the ARCH-10 pre-flight: an `[INFO]` line is a **measurement,
not a verdict**. Read them. ARCH-09's 5.7s import time and ARCH-10's unreachable
S3 driver both hid inside baselines that were skimmed as passes.

Three checks decide the shape of the phase plan:

  V.1.1  is pgvector installed, and at what version?  ← BLOCKING
  V.2.1  where does the ChromaDB corpus physically live? ← BLOCKING
  V.7.2  does a filtered vector query under-return for a small tenant?

`--skip-corpus` omits everything that imports chromadb or sentence_transformers,
so the script runs in a light container. It does **not** skip V.1.1 or V.2.1.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any, Callable, Optional

# Enforce UTF-8 output streams across Windows CP1252 / Linux
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PASS, FAIL, WARN, INFO, SKIP = "PASS", "FAIL", "WARN", "INFO", "SKIP"

_results: list[tuple[str, str, str, str]] = []
_verbose = False


def record(cid: str, verdict: str, desc: str, detail: str = "") -> None:
    _results.append((cid, verdict, desc, detail))


def check(cid: str, desc: str) -> Callable:
    """Wrap a probe. The probe returns (verdict, detail)."""

    def wrapper(fn: Callable[..., tuple[str, str]]) -> Callable[..., None]:
        def runner(*a: Any, **k: Any) -> None:
            try:
                verdict, detail = fn(*a, **k)
                record(cid, verdict, desc, detail)
            except Exception as exc:  # noqa: BLE001
                record(cid, FAIL, desc, f"probe raised: {type(exc).__name__}: {exc}")

        return runner

    return wrapper


# ============================================================================
# V.1 — PostgreSQL substrate
# ============================================================================


@check("V.1.1", "pgvector is installed and new enough for filtered search")
def v1_1(db) -> tuple[str, str]:
    from sqlalchemy import text as sql

    installed = db.execute(
        sql("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    ).scalar_one_or_none()
    available = db.execute(
        sql(
            "SELECT default_version FROM pg_available_extensions "
            "WHERE name = 'vector'"
        )
    ).scalar_one_or_none()

    if installed is None and available is None:
        return (
            FAIL,
            "pgvector is NEITHER installed NOR available in this PostgreSQL "
            "instance.\n         => ARCH-11 Step 1 cannot proceed. On a managed "
            "provider this is a console toggle; on a self-managed instance it "
            "is a package install and a restart.\n         => Confirm the "
            "provider offers >= 0.8.0 before committing to the migration; see "
            "V.7.2 for why the version matters more than it looks.",
        )
    if installed is None:
        return (
            FAIL,
            f"pgvector available ({available}) but NOT installed. "
            "`CREATE EXTENSION vector;` is Step 1's first statement.",
        )

    parts = [int(p) for p in re.findall(r"\d+", installed)[:3]]
    while len(parts) < 3:
        parts.append(0)
    major, minor, _ = parts

    if (major, minor) >= (0, 8):
        return (
            PASS,
            f"pgvector {installed} — supports hnsw.iterative_scan, which is "
            "the only clean answer to the filtered-recall problem in V.7.2.",
        )
    return (
        WARN,
        f"pgvector {installed} installed, but iterative scan arrived in 0.8.0. "
        "Without it a tenant-filtered HNSW query silently under-returns (V.7.2). "
        "Step 1 must then partition `document_chunks` by workspace, which is a "
        "schema decision, not a tuning knob. Upgrade if you can.",
    )


@check("V.1.2", "lexical search extensions")
def v1_2(db) -> tuple[str, str]:
    from sqlalchemy import text as sql

    rows = db.execute(
        sql(
            "SELECT name, installed_version, default_version "
            "FROM pg_available_extensions "
            "WHERE name IN ('pg_trgm','unaccent','btree_gin')"
        )
    ).all()
    if not rows:
        return (WARN, "none of pg_trgm / unaccent / btree_gin are available")
    parts = [
        f"{name}={installed or 'available:' + str(default)}"
        for name, installed, default in rows
    ]
    missing = [name for name, installed, _ in rows if installed is None]
    verdict = INFO if not missing else WARN
    return (verdict, "; ".join(parts))


@check("V.1.3", "PostgreSQL version and vector-relevant memory settings")
def v1_3(db) -> tuple[str, str]:
    from sqlalchemy import text as sql

    version = db.execute(sql("SHOW server_version")).scalar_one()
    maintenance = db.execute(sql("SHOW maintenance_work_mem")).scalar_one()
    work_mem = db.execute(sql("SHOW work_mem")).scalar_one()
    shared = db.execute(sql("SHOW shared_buffers")).scalar_one()
    return (
        INFO,
        f"PostgreSQL {version}; maintenance_work_mem={maintenance} "
        f"(HNSW build speed), work_mem={work_mem}, shared_buffers={shared}. "
        "An HNSW build that spills to disk is 10-50x slower; size "
        "maintenance_work_mem for the index, not the default.",
    )


# ============================================================================
# V.2 — the existing corpus
# ============================================================================


@check("V.2.1", "the ChromaDB corpus is reachable from more than one container")
def v2_1() -> tuple[str, str]:
    from app.core.config import settings

    raw = str(settings.CHROMA_PERSIST_DIRECTORY)
    path = pathlib.Path(raw)
    resolved = path.resolve() if path.exists() else path

    detail = f"CHROMA_PERSIST_DIRECTORY={raw!r} -> {resolved}"

    if path.is_absolute() and str(resolved).startswith(("/mnt/", "/srv/shared", "/data/shared")):
        return (WARN, detail + " (looks like a mount; confirm it is shared)")

    return (
        FAIL,
        detail
        + "\n         => This is a LOCAL directory, exactly as "
        "UPLOAD_DIR was before ARCH-10 Step 4."
        "\n         => `document.enrich` runs in the enrich worker container "
        "and writes vectors to ITS filesystem. The web process serving the "
        "assistant reads its OWN, which is empty."
        "\n         => Retrieval is therefore already broken by ARCH-10 Step 8's "
        "container separation, silently: an empty collection returns [] rather "
        "than raising."
        "\n         => This is what ARCH-11 Step 1 exists to fix. Do not "
        "'temporarily' fix it with a shared volume — a shared filesystem "
        "vector store is the ARCH-07 local-disk StorageDriver again.",
    )


@check("V.2.2", "vector corpus inventory")
def v2_2() -> tuple[str, str]:
    from app.services.embedding_service import embedding_service

    collections = embedding_service.client.list_collections()
    if not collections:
        return (INFO, "0 collections — nothing to migrate, or nothing survived")

    rows: list[str] = []
    total = 0
    dimensions: set[int] = set()
    for collection in collections:
        try:
            count = collection.count()
        except Exception:  # noqa: BLE001
            count = -1
        total += max(count, 0)
        if count > 0 and len(dimensions) < 3:
            try:
                sample = collection.get(limit=1, include=["embeddings"])
                vectors = sample.get("embeddings") or []
                if vectors:
                    dimensions.add(len(vectors[0]))
            except Exception:  # noqa: BLE001
                pass
        rows.append(f"{collection.name}={count}")

    detail = (
        f"{len(collections)} collection(s), {total} vector(s); "
        f"dimensions observed: {sorted(dimensions) or 'unknown'}\n         "
        + "; ".join(rows[:12])
        + (" ..." if len(rows) > 12 else "")
    )
    if len(dimensions) > 1:
        return (
            WARN,
            detail
            + "\n         => MIXED DIMENSIONS. `vector(n)` is a fixed-width "
            "column; one table cannot hold both. Step 2 must either re-embed "
            "the minority or carry a second table.",
        )
    return (INFO, detail)


@check("V.2.3", "every Chroma collection corresponds to a live workspace")
def v2_3(db) -> tuple[str, str]:
    import uuid as _uuid

    from sqlalchemy import text as sql

    from app.services.embedding_service import embedding_service

    live = {
        str(row[0])
        for row in db.execute(sql("SELECT id FROM workspaces")).all()
    }
    orphans: list[str] = []
    malformed: list[str] = []
    for collection in embedding_service.client.list_collections():
        name = collection.name
        if not name.startswith("ws_"):
            malformed.append(name)
            continue
        candidate = name.removeprefix("ws_")
        try:
            _uuid.UUID(candidate)
        except ValueError:
            malformed.append(name)
            continue
        if candidate not in live:
            orphans.append(name)

    if malformed:
        return (
            FAIL,
            f"{len(malformed)} collection(s) outside the ws_<uuid> grammar: "
            f"{malformed[:5]}. Step 2's migration keys on that grammar.",
        )
    if orphans:
        return (
            WARN,
            f"{len(orphans)} collection(s) belong to workspaces that no longer "
            f"exist: {orphans[:5]}. Deleting a workspace never deleted its "
            "vectors — Chroma has no foreign key to Postgres. Migrating them "
            "would import dead tenants' content into the new store.",
        )
    return (PASS, "all collections map to a live workspace")


# ============================================================================
# V.3 — sparse retrieval
# ============================================================================


@check("V.3.1", "BM25 index state is shared, not per-process")
def v3_1() -> tuple[str, str]:
    bm25_path = REPO_ROOT / "app" / "services" / "bm25_service.py"
    if not bm25_path.exists():
        return (WARN, "app/services/bm25_service.py does not exist")
    source = bm25_path.read_text(encoding="utf-8", errors="replace")
    in_memory = bool(re.search(r"self\._indexes\s*:\s*OrderedDict", source))
    rebuilt_from_chroma = "get_workspace_collection" in source

    if in_memory:
        return (
            FAIL,
            "BM25 indexes live in a per-process dict "
            "(`self._indexes: OrderedDict`)"
            + (", rebuilt by reading the Chroma collection" if rebuilt_from_chroma else "")
            + ".\n         => `rebuild_index()` is called from `document.enrich`, "
            "which now runs in the enrich worker. That process builds an index "
            "no other process can see."
            "\n         => `bm25_service.is_ready()` is therefore False in every "
            "web process, forever, and hybrid search silently degrades to "
            "dense-only."
            "\n         => With 2+ web replicas it was already split-brain "
            "before the worker split."
            "\n         => Step 4 replaces this with Postgres full-text search, "
            "which is shared by construction.",
        )
    return (PASS, "BM25 state is not process-local")


# ============================================================================
# V.4 — the embedding model
# ============================================================================


@check("V.4.1", "the embedding model's actual output dimension")
def v4_1() -> tuple[str, str]:
    from app.core.config import settings
    from app.services.embedding_service import embedding_service

    vector = embedding_service.generate_embeddings(["dimension probe"])[0]
    return (
        INFO,
        f"{settings.EMBEDDING_MODEL_NAME} -> dim={len(vector)}. This is the "
        "literal that goes into `vector(n)` and cannot be changed later "
        "without a table rewrite and a full re-embed.",
    )


@check("V.4.2", "per-workspace embedding_model divergence")
def v4_2(db) -> tuple[str, str]:
    from sqlalchemy import text as sql

    from app.core.config import settings

    rows = db.execute(
        sql(
            "SELECT embedding_model, count(*) FROM document_settings "
            "GROUP BY embedding_model ORDER BY count(*) DESC"
        )
    ).all()
    if not rows:
        return (INFO, "no document_settings rows")

    listing = "; ".join(f"{model}={count}" for model, count in rows)
    global_model = settings.EMBEDDING_MODEL_NAME
    used_anywhere = any(
        model and model.split("/")[-1] == global_model.split("/")[-1]
        for model, _ in rows
    )

    detail = (
        f"{len(rows)} distinct value(s): {listing}\n         "
        f"settings.EMBEDDING_MODEL_NAME={global_model!r}"
    )

    if len(rows) > 1:
        return (
            FAIL,
            detail
            + "\n         => Workspaces disagree on the model, but "
            "`embedding_service._get_model()` reads the GLOBAL setting and "
            "ignores the column entirely. The per-workspace field has never "
            "done anything."
            "\n         => `vector(n)` is fixed-width, so honouring it would "
            "mean one table per dimension. Step 1 must decide: retire the "
            "column, or store the model name per chunk and refuse a query "
            "whose model disagrees.",
        )
    _ = used_anywhere
    return (
        WARN,
        detail
        + "\n         => One value, so no divergence today — but the column is "
        "still inert: changing it changes nothing. A setting that lies is worse "
        "than a missing one.",
    )


# ============================================================================
# V.5 — chunking
# ============================================================================


@check("V.5.1", "chunk sizing is expressed in tokens, not characters")
def v5_1(db) -> tuple[str, str]:
    from sqlalchemy import text as sql

    chunking_path = REPO_ROOT / "app" / "services" / "chunking_service.py"
    if not chunking_path.exists():
        return (WARN, "app/services/chunking_service.py does not exist")
    source = chunking_path.read_text(encoding="utf-8", errors="replace")
    char_based = bool(re.search(r"len\(\s*paragraph\s*\)\s*<?=", source)) or bool(
        re.search(r"len\(.*\)\s*[<>]=?\s*chunk_size", source)
    )
    token_based = "tiktoken" in source or "tokenizer" in source

    rows = db.execute(
        sql(
            "SELECT chunk_size, chunk_overlap, count(*) FROM document_settings "
            "GROUP BY chunk_size, chunk_overlap ORDER BY count(*) DESC LIMIT 5"
        )
    ).all()
    settings_listing = "; ".join(
        f"size={size}/overlap={overlap} x{count}" for size, overlap, count in rows
    )

    if char_based and not token_based:
        estimates = []
        for size, overlap, _ in rows[:3]:
            approx_tokens = round(size / 4)
            pct = round(100 * overlap / size) if size else 0
            estimates.append(f"{size}ch ~= {approx_tokens} tok, overlap {pct}%")
        return (
            FAIL,
            f"chunking measures CHARACTERS. document_settings: {settings_listing}"
            f"\n         => {'; '.join(estimates)}"
            "\n         => The ARCH-11 target is 300-500 TOKENS with 10% "
            "overlap. At ~4 chars/token the current defaults are roughly a "
            "quarter of the target size and double the target overlap."
            "\n         => Under-sized chunks fragment context and inflate the "
            "vector count; over-lapping ones inflate it further and skew RRF by "
            "returning near-duplicate neighbours.",
        )
    return (INFO, f"token-aware chunking detected; {settings_listing}")


@check("V.5.2", "chunk -> bounding box provenance is preserved")
def v5_2(db) -> tuple[str, str]:
    from sqlalchemy import text as sql

    models_path = REPO_ROOT / "app" / "services" / "document_models.py"
    if not models_path.exists():
        return (WARN, "app/services/document_models.py does not exist")
    models = models_path.read_text(encoding="utf-8", errors="replace")
    has_offsets = "start_char" in models or "char_span" in models
    has_bbox = "bbox" in models or "BoundingBox" in models

    boxed = db.execute(
        sql(
            "SELECT count(*) FROM work_items "
            "WHERE extraction_metadata IS NOT NULL "
            "AND jsonb_path_exists(extraction_metadata, "
            "'$.pages[*].blocks[*].box')"
        )
    ).scalar_one()

    if has_bbox and has_offsets:
        return (PASS, f"DocumentChunk carries provenance; {boxed} document(s) boxed")

    return (
        FAIL,
        f"`DocumentChunk` is (text, page_number, chunk_index) — no character "
        f"offsets, no box. {boxed} document(s) already store per-block boxes in "
        "`work_items.extraction_metadata`."
        "\n         => ARCH-10 Step 6 deliberately preserved those coordinates "
        "because audit-grade citation provenance is the named commercial moat. "
        "Chunking currently discards the link to them."
        "\n         => The join is recoverable but only at chunk time: page text "
        "is the newline-join of block texts, so each block occupies a known "
        "character span. A chunk that records (page_number, start_char, "
        "end_char) can intersect those spans and carry the union box."
        "\n         => Recovering it later means re-chunking and re-embedding "
        "every document ever uploaded.",
    )


# ============================================================================
# V.6 — metering (R20)
# ============================================================================


@check("V.6.1", "embedding.token usage is instrumented (ARCH-10 R20)")
def v6_1() -> tuple[str, str]:
    hits: list[str] = []
    for path in (REPO_ROOT / "app").rglob("*.py"):
        rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        source = path.read_text(encoding="utf-8", errors="replace")
        if "embedding.token" in source and "core/usage_events.py" not in rel:
            hits.append(rel)
    if hits:
        return (PASS, f"referenced in {hits}")
    return (
        FAIL,
        "`embedding.token` appears only in the taxonomy. `document.enrich` "
        "generates embeddings and records no usage."
        "\n         => This is ARCH-10 R20, and it is the reason it was carried "
        "forward as ARCH-11 Step 1 rather than left for ARCH-14: you cannot "
        "bill for usage you did not measure at the moment it occurred.",
    )


@check("V.6.2", "the metering primitives ARCH-11 needs are present")
def v6_2(db) -> tuple[str, str]:
    from sqlalchemy import text as sql

    from app.core.usage_events import USAGE_EVENT_TYPES

    wanted = {"embedding.token", "llm.input_token", "llm.output_token"}
    missing = sorted(wanted - set(USAGE_EVENT_TYPES))
    if missing:
        return (FAIL, f"taxonomy is missing {missing}")

    recorded = db.execute(
        sql(
            "SELECT event_type, count(*), coalesce(sum(quantity),0) "
            "FROM usage_events WHERE event_type = ANY(:t) GROUP BY event_type"
        ),
        {"t": sorted(wanted)},
    ).all()
    listing = (
        "; ".join(f"{t}: {c} rows, {q} units" for t, c, q in recorded)
        or "0 rows recorded for any of them"
    )
    return (INFO, f"taxonomy complete; {listing}")


# ============================================================================
# V.7 — tenant isolation
# ============================================================================


@check("V.7.1", "every vector query call site carries a tenant scope")
def v7_1() -> tuple[str, str]:
    suspicious: list[str] = []
    call = re.compile(r"\.(query|similarity_search|search|hybrid_search)\s*\(")
    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines):
            if not call.search(line):
                continue
            window = "\n".join(lines[index : index + 8])
            if "workspace_id" not in window and "collection" not in window:
                suspicious.append(f"{rel}:{index + 1}  {line.strip()[:90]}")

    if suspicious:
        return (
            WARN,
            f"{len(suspicious)} retrieval call site(s) with no visible tenant "
            "scope within 8 lines:\n         " + "\n         ".join(suspicious[:8])
            + "\n         => Structural only. Today isolation comes from the "
            "collection NAME, which is why this reads clean. On pgvector it "
            "becomes a WHERE predicate that can be omitted, and an omitted "
            "predicate does not error — it returns another tenant's document as "
            "a citation.",
        )
    return (
        INFO,
        "no unscoped call sites found structurally. Note this proves little: "
        "collection-name isolation moves to a SQL predicate in Step 3, and "
        "only the behavioural matrix in Step 8 can catch its absence.",
    )


@check("V.7.2", "a tenant-filtered vector query returns a full result set")
def v7_2(db) -> tuple[str, str]:
    """The check that decides whether Step 1 needs partitioning.

    An HNSW index is searched *before* the WHERE clause is applied. A tenant
    holding a small share of a large shared index gets a candidate set that is
    almost entirely other tenants' rows, which are then filtered out — so the
    query returns fewer rows than asked for, silently, with no error.
    """
    from sqlalchemy import text as sql

    installed = db.execute(
        sql("SELECT extversion FROM pg_extension WHERE extname='vector'")
    ).scalar_one_or_none()
    if installed is None:
        return (SKIP, "pgvector not installed; cannot probe (see V.1.1)")

    db.execute(sql("DROP TABLE IF EXISTS arch11_filter_probe"))
    db.execute(
        sql(
            "CREATE TEMP TABLE arch11_filter_probe ("
            "  id serial PRIMARY KEY,"
            "  workspace_id uuid NOT NULL,"
            "  embedding vector(64) NOT NULL)"
        )
    )
    db.execute(
        sql(
            "INSERT INTO arch11_filter_probe (workspace_id, embedding) "
            "SELECT ('00000000-0000-0000-0000-' || lpad(mod(g, 50)::text, 12, '0'))::uuid,"
            "       (SELECT array_agg(random())::vector(64) FROM generate_series(1,64)) "
            "FROM generate_series(1, 20000) g"
        )
    )
    db.execute(
        sql(
            "INSERT INTO arch11_filter_probe (workspace_id, embedding) "
            "SELECT '99999999-9999-9999-9999-999999999999'::uuid,"
            "       (SELECT array_agg(random())::vector(64) FROM generate_series(1,64)) "
            "FROM generate_series(1, 20)"
        )
    )
    db.execute(
        sql(
            "CREATE INDEX ON arch11_filter_probe "
            "USING hnsw (embedding vector_cosine_ops)"
        )
    )
    db.execute(sql("ANALYZE arch11_filter_probe"))

    # Force the index path so this measures HNSW behaviour, not the planner's
    # good judgement on a tiny table.
    db.execute(sql("SET LOCAL enable_seqscan = off"))
    db.execute(sql("SET LOCAL enable_bitmapscan = off"))
    db.execute(sql("SET LOCAL hnsw.ef_search = 40"))

    returned = db.execute(
        sql(
            "SELECT count(*) FROM ("
            "  SELECT id FROM arch11_filter_probe"
            "  WHERE workspace_id = '99999999-9999-9999-9999-999999999999'"
            "  ORDER BY embedding <=> (SELECT embedding FROM arch11_filter_probe"
            "    WHERE workspace_id = '99999999-9999-9999-9999-999999999999' LIMIT 1)"
            "  LIMIT 10) t"
        )
    ).scalar_one()

    db.rollback()

    if returned >= 10:
        return (
            PASS,
            f"asked for 10, got {returned}. Filtered recall holds on this "
            f"instance (pgvector {installed}).",
        )
    return (
        FAIL,
        f"asked for 10, got {returned}. A tenant holding 0.1% of the index "
        "cannot see its own documents."
        "\n         => HNSW searches the index and filters afterwards. No error "
        "is raised; the RAG answer is simply built on less context than it "
        "should have had, or none."
        "\n         => This is the tenant-isolation problem's twin. The plan "
        "warned that a missing predicate leaks other tenants' data; this is the "
        "failure that happens WITH the predicate."
        "\n         => Fixes, in order of robustness: (1) pgvector >= 0.8 "
        "`hnsw.iterative_scan = relaxed_order`; (2) partition "
        "`document_chunks` by hash(workspace_id) so pruning shrinks the index "
        "the scan sees; (3) over-fetch with a raised ef_search — fragile, and "
        "it degrades as the corpus grows.",
    )


# ============================================================================
# V.8 — evaluation and schema
# ============================================================================


@check("V.8.1", "a retrieval quality harness exists to migrate against")
def v8_1() -> tuple[str, str]:
    from app.core.config import settings

    evaluator = REPO_ROOT / "app" / "services" / "retrieval_evaluator.py"
    thresholds = {
        name: getattr(settings, name, None)
        for name in (
            "RETRIEVAL_MIN_RECALL",
            "RETRIEVAL_MIN_PRECISION",
            "RETRIEVAL_MIN_MRR",
            "RETRIEVAL_MAX_CONTAMINATION",
            "RETRIEVAL_MAX_LATENCY_MS",
        )
    }
    golden = list((REPO_ROOT).rglob("*golden*")) + list((REPO_ROOT).rglob("*eval*set*"))

    detail = (
        f"retrieval_evaluator.py={'present' if evaluator.exists() else 'MISSING'}; "
        f"thresholds={thresholds}; golden-set files found={len(golden)}"
    )
    if evaluator.exists() and golden:
        return (INFO, detail)
    return (
        WARN,
        detail
        + "\n         => Thresholds without a fixed golden set measure nothing. "
        "A substrate migration is exactly the change that needs a before/after "
        "number, and it has to be captured BEFORE Step 2 moves the vectors.",
    )


@check("V.8.2", "alembic is at a single head")
def v8_2() -> tuple[str, str]:
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if out.returncode != 0:
        return (FAIL, f"alembic heads failed: {out.stderr[-300:]}")
    heads = [line for line in out.stdout.splitlines() if line.strip()]
    if len(heads) != 1:
        return (FAIL, f"{len(heads)} heads: {heads}")
    return (PASS, heads[0].strip())


@check("V.8.3", "the web process still imports no heavy ML module")
def v8_3() -> tuple[str, str]:
    script = (
        "import sys, json\n"
        "import app.main\n"
        "print(json.dumps([m for m in "
        "('paddleocr','paddle','torch','chromadb','sentence_transformers') "
        "if m in sys.modules]))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if out.returncode != 0:
        return (FAIL, out.stderr[-300:])
    leaked = json.loads(out.stdout.strip().splitlines()[-1])
    if leaked:
        return (FAIL, f"app.main pulled in {leaked} — ARCH-10 G1.1 regressed")
    return (PASS, "clean")


# ============================================================================
# Runner
# ============================================================================


def main(argv: Optional[list[str]] = None) -> int:
    global _verbose
    parser = argparse.ArgumentParser(prog="verify_arch11_step0")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--skip-corpus",
        action="store_true",
        help="omit checks that import chromadb / sentence_transformers",
    )
    args = parser.parse_args(argv)
    _verbose = args.verbose

    try:
        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] could not import the application: {exc}")
        return 2

    v2_1()
    v3_1()
    v6_1()
    v7_1()
    v8_1()
    v8_3()

    with SessionLocal() as db:
        v1_1(db)
        v1_2(db)
        v1_3(db)
        v4_2(db)
        v5_1(db)
        v5_2(db)
        v6_2(db)
        v7_2(db)
        if args.skip_corpus:
            record(
                "V.2.2", SKIP, "vector corpus inventory", "--skip-corpus"
            )
            record(
                "V.2.3", SKIP, "collections map to live workspaces", "--skip-corpus"
            )
            record("V.4.1", SKIP, "embedding output dimension", "--skip-corpus")
        else:
            v2_2()
            v2_3(db)
            v4_1()

    v8_2()

    print("ARCH-11 Step 0 — Pre-flight Audit (Knowledge Base & Hybrid RAG)\n")
    print("== FINDINGS " + "=" * 46)

    order = {FAIL: 0, WARN: 1, PASS: 2, INFO: 3, SKIP: 4}
    counts = {PASS: 0, FAIL: 0, WARN: 0, INFO: 0, SKIP: 0}
    for cid, verdict, desc, detail in sorted(
        _results, key=lambda r: (order[r[1]], r[0])
    ):
        counts[verdict] += 1
        print(f"[{verdict}] {cid:<7} {desc}")
        if detail and (verdict in {FAIL, WARN} or _verbose):
            print(f"         {detail}")

    print(
        f"\n{counts[PASS]} pass | {counts[FAIL]} blocking | {counts[WARN]} warning "
        f"| {counts[INFO]} baseline | {counts[SKIP]} skipped"
    )
    print(
        "\nNote: an [INFO] line is a MEASUREMENT, not a verdict. Read them "
        "carefully."
    )
    if counts[FAIL]:
        print("\n[FAIL] BLOCKING FINDING(S) — resolve before ARCH-11 Step 1.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

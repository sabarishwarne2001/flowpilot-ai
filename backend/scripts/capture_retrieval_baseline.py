#!/usr/bin/env python
"""ARCH-11 Step 1 — capture the frozen retrieval baseline.

    python scripts/capture_retrieval_baseline.py
    python scripts/capture_retrieval_baseline.py --top-k 10 --label dense-only
    python scripts/capture_retrieval_baseline.py --acknowledge-empty-corpus

Writes `evaluation/baselines/retrieval-baseline-<UTC>.json` and refreshes
`evaluation/baselines/latest.json`. Both are committed. The numbers in them are
the gate for Steps 5-9.

## Why this script attests to its environment before it measures anything

§0.1 says retrieval may be **silently broken right now**: the enrich worker
writes Chroma vectors to its own filesystem and the web process reads its own,
which is empty. An empty collection does not raise — it returns `[]`, the RRF
merge produces nothing, and the assistant answers from no context, fluently.
§0.2 says the lexical arm has been dead for longer than that, because
`BM25Service._indexes` is a per-process `OrderedDict` rebuilt only inside the
enrich worker.

A baseline captured on top of either condition is an accidental zero, and every
later step measures its "improvement" against it. So this script records, in
the output file and on stdout:

- the **absolute resolved path** of the Chroma directory this process opened;
- the **vector count per workspace collection** it can actually see;
- whether `bm25_service.is_ready()` is True in this process;
- which retrieval arms therefore contributed.

If a workspace in the golden set has zero visible vectors, the script **stops**.
`--acknowledge-empty-corpus` proceeds anyway and stamps
`"corpus_state": "EMPTY_ACKNOWLEDGED"` in the output, which is the honest thing
to record if you decide the zero is itself the finding.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Enforce UTF-8 output streams across Windows CP1252 / Linux
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings  # noqa: E402
from app.evaluation.golden_set import (  # noqa: E402
    GoldenSet,
    GoldenSetError,
    load_golden_set,
)
from app.evaluation.retrieval_metrics import (  # noqa: E402
    QuestionResult,
    aggregate,
    score_question,
)

BASELINE_LABEL_DEFAULT = "dense-only"


# ===========================================================================
# Environment attestation
# ===========================================================================


def _git_commit() -> Optional[str]:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:  # noqa: BLE001
        return None


def _chroma_state(golden: GoldenSet) -> dict[str, Any]:
    from app.services.embedding_service import embedding_service, workspace_collection_name

    resolved = Path(settings.CHROMA_PERSIST_DIRECTORY).resolve()
    state: dict[str, Any] = {
        "configured_path": settings.CHROMA_PERSIST_DIRECTORY,
        "resolved_path": str(resolved),
        "path_is_relative": not Path(settings.CHROMA_PERSIST_DIRECTORY).is_absolute(),
        "cwd": os.getcwd(),
        "exists": resolved.exists(),
        "per_workspace": {},
        "total_collections": None,
        "error": None,
    }
    try:
        client = embedding_service.client
        collections = client.list_collections()
        state["total_collections"] = len(collections)
        for workspace in golden.workspaces:
            name = workspace_collection_name(workspace.workspace_id)
            try:
                count = embedding_service.get_workspace_collection(
                    workspace.workspace_id
                ).count()
            except Exception as exc:  # noqa: BLE001
                count = None
                state.setdefault("collection_errors", {})[name] = str(exc)
            state["per_workspace"][workspace.alias] = {
                "collection": name,
                "vectors": count,
            }
    except Exception as exc:  # noqa: BLE001
        state["error"] = f"{type(exc).__name__}: {exc}"
    return state


def _bm25_state(golden: GoldenSet) -> dict[str, Any]:
    """`is_ready` is per workspace: `is_ready(*, workspace_id=...)`.

    Asking it once globally would be a TypeError, and asking it about a
    workspace whose index this process never built is the whole finding — the
    index is an in-process `OrderedDict` rebuilt only inside `document.enrich`,
    so in a web process it is permanently empty and the lexical arm of
    `hybrid_search` contributes nothing.
    """
    try:
        from app.services.bm25_service import bm25_service

        per_workspace = {
            workspace.alias: bool(
                bm25_service.is_ready(workspace_id=workspace.workspace_id)
            )
            for workspace in golden.workspaces
        }
        indexes = getattr(bm25_service, "_indexes", None)
        return {
            "per_workspace": per_workspace,
            "any_ready_in_this_process": any(per_workspace.values()),
            "indexes_held": len(indexes) if indexes is not None else None,
            "note": (
                "Process-local OrderedDict rebuilt only from document.enrich. "
                "All-False here means this baseline is dense-only "
                "(ARCH-11 §0.2)."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "per_workspace": {},
            "any_ready_in_this_process": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _config_snapshot() -> dict[str, Any]:
    from app.core.embeddings import active_max_sequence_tokens, active_model_name

    return {
        "embedding_model_setting": settings.EMBEDDING_MODEL_NAME,
        "embedding_model_canonical": active_model_name(),
        "embedding_max_sequence_tokens": active_max_sequence_tokens(),
        "embedding_batch_size": settings.EMBEDDING_BATCH_SIZE,
        "chunk_size_default": settings.CHUNK_SIZE,
        "chunk_overlap_default": settings.CHUNK_OVERLAP,
        "rag_top_k": settings.RAG_TOP_K,
        "rag_similarity_threshold": settings.RAG_SIMILARITY_THRESHOLD,
        "rerank_max_candidates": settings.RERANK_MAX_CANDIDATES,
        "rerank_final_results": settings.RERANK_FINAL_RESULTS,
        "thresholds": {
            "RETRIEVAL_MIN_RECALL": settings.RETRIEVAL_MIN_RECALL,
            "RETRIEVAL_MIN_PRECISION": settings.RETRIEVAL_MIN_PRECISION,
            "RETRIEVAL_MIN_MRR": settings.RETRIEVAL_MIN_MRR,
            "RETRIEVAL_MAX_CONTAMINATION": settings.RETRIEVAL_MAX_CONTAMINATION,
            "RETRIEVAL_MAX_LATENCY_MS": settings.RETRIEVAL_MAX_LATENCY_MS,
        },
    }


# ===========================================================================
# The run
# ===========================================================================


def run_question(
    question,
    *,
    golden: GoldenSet,
    top_k: int,
    similarity_threshold: float,
) -> QuestionResult:
    from app.services.retrieval_service import retrieval_service

    workspace = golden.workspace(question.workspace_alias)
    work_item_ids = golden.work_item_ids_for_workspace(question.workspace_alias)

    started = time.perf_counter()
    try:
        results = retrieval_service.hybrid_search(
            workspace_id=workspace.workspace_id,
            query=question.query,
            work_item_ids=work_item_ids,
            top_k=top_k,
            similarity_threshold=similarity_threshold,
        )
        error = None
    except Exception as exc:  # noqa: BLE001
        results = []
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - started) * 1000.0

    return score_question(
        question, results, golden=golden, latency_ms=latency_ms, error=error
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", default=settings.GOLDEN_SET_PATH)
    parser.add_argument("--out-dir", default=settings.RETRIEVAL_BASELINE_DIR)
    parser.add_argument("--top-k", type=int, default=settings.RAG_TOP_K)
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=settings.RAG_SIMILARITY_THRESHOLD,
    )
    parser.add_argument(
        "--label",
        default=BASELINE_LABEL_DEFAULT,
        help=(
            "Stamped into the file. Leave as 'dense-only' unless you have "
            "verified bm25_service.is_ready() is True in THIS process."
        ),
    )
    parser.add_argument("--acknowledge-empty-corpus", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Do not write files.")
    args = parser.parse_args()

    try:
        golden = load_golden_set(args.golden)
    except GoldenSetError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    print(f"[INFO] golden set   : {golden.path} ({golden.sha256[:12]})")
    print(
        f"[INFO] contract     : {len(golden.questions)} questions, "
        f"{len(golden.documents)} documents, {len(golden.workspaces)} workspaces"
    )

    chroma = _chroma_state(golden)
    bm25 = _bm25_state(golden)

    print(f"[INFO] chroma path  : {chroma['resolved_path']}")
    if chroma["path_is_relative"]:
        print(
            "[WARN] CHROMA_PERSIST_DIRECTORY is relative; this process resolved "
            f"it against cwd={chroma['cwd']}. A worker started from a different "
            "directory reads a different store (ARCH-11 §0.1)."
        )
    for alias, info in chroma["per_workspace"].items():
        print(f"[INFO] vectors[{alias}] : {info['vectors']}")
    print(f"[INFO] bm25 ready   : {bm25.get('per_workspace')}")

    empty = [
        alias
        for alias, info in chroma["per_workspace"].items()
        if not info.get("vectors")
    ]
    corpus_state = "POPULATED"
    if empty:
        corpus_state = "EMPTY_ACKNOWLEDGED" if args.acknowledge_empty_corpus else "EMPTY"
        message = (
            f"[FAIL] workspaces with zero visible vectors: {', '.join(empty)}. "
            "A baseline captured here is an accidental zero and every later "
            "step would measure its improvement against it (R28). Verify §0.1 "
            "in this process first, or re-run with --acknowledge-empty-corpus "
            "to record the zero deliberately."
        )
        if not args.acknowledge_empty_corpus:
            print(message, file=sys.stderr)
            return 3
        print(message.replace("[FAIL]", "[WARN]"))

    arms = {
        "dense": True,
        "lexical": bool(bm25.get("any_ready_in_this_process")),
    }
    label = args.label
    if arms["lexical"] and label == BASELINE_LABEL_DEFAULT:
        print(
            "[WARN] bm25_service reports a live index in this process but the "
            "label is "
            "still 'dense-only'. Pass --label hybrid if that is genuinely true; "
            "a mislabelled baseline makes the Step 6 delta unreadable (R29)."
        )

    results: list[QuestionResult] = []
    for index, question in enumerate(golden.questions, start=1):
        row = run_question(
            question,
            golden=golden,
            top_k=args.top_k,
            similarity_threshold=args.similarity_threshold,
        )
        results.append(row)
        flag = "!" if (row.error or row.cross_tenant_hits) else " "
        print(
            f"[{index:>3}/{len(golden.questions)}]{flag} {question.id:<8} "
            f"recall={row.span_recall:.2f} mrr={row.mrr:.2f} "
            f"prec={row.chunk_precision:.2f} n={row.retrieved} "
            f"{row.latency_ms:.0f}ms"
        )

    summary = aggregate(results)
    captured_at = datetime.now(timezone.utc)

    document = {
        "schema": "arch11-retrieval-baseline-v1",
        "label": label,
        "captured_at": captured_at.isoformat(),
        "corpus_state": corpus_state,
        "retrieval_arms": arms,
        "arms_note": (
            "Dense-only unless retrieval_arms.lexical is true. BM25 state is "
            "process-local (ARCH-11 §0.2); a Step 6 hybrid number is not "
            "comparable to this one as a pure ranking improvement."
        ),
        "golden_set": golden.fingerprint(),
        "parameters": {
            "top_k": args.top_k,
            "similarity_threshold": args.similarity_threshold,
        },
        "environment": {
            "git_commit": _git_commit(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "hostname": platform.node(),
            "chroma": chroma,
            "bm25": bm25,
            "config": _config_snapshot(),
        },
        "summary": summary,
        "questions": [row.as_dict() for row in results],
    }

    print("\n" + "=" * 72)
    print(f"label              : {label}")
    print(f"corpus             : {corpus_state}")
    print(f"span recall        : {summary.get('span_recall')}")
    print(f"document recall    : {summary.get('document_recall')}")
    print(f"chunk precision    : {summary.get('chunk_precision')}")
    print(f"MRR                : {summary.get('mrr')}")
    print(f"contamination      : {summary.get('contamination')}")
    print(f"cross-tenant hits  : {summary.get('cross_tenant_hits_total')}")
    print(f"zero-result queries: {summary.get('zero_result_questions')}")
    print(f"latency p95 ms     : {summary.get('latency_ms', {}).get('p95')}")
    print("=" * 72)

    if summary.get("cross_tenant_hits_total"):
        print(
            "[FAIL] the current stack returned chunks from a foreign workspace. "
            "This is a live tenant isolation defect and it predates the "
            "migration. Stop and fix it before Step 2.",
            file=sys.stderr,
        )

    if args.dry_run:
        print("[INFO] --dry-run: nothing written.")
        return 0

    out_dir = REPO_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = captured_at.strftime("%Y%m%dT%H%M%SZ")
    target = out_dir / f"retrieval-baseline-{stamp}.json"
    payload = json.dumps(document, indent=2, sort_keys=False)
    target.write_text(payload, encoding="utf-8")
    (out_dir / "latest.json").write_text(payload, encoding="utf-8")

    print(f"[INFO] wrote {target}")
    print(f"[INFO] wrote {out_dir / 'latest.json'}")
    print("[INFO] commit both. Steps 5-9 gate against them.")

    return 1 if summary.get("cross_tenant_hits_total") else 0


if __name__ == "__main__":
    raise SystemExit(main())
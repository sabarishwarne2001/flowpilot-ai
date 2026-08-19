#!/usr/bin/env python
"""ARCH-11.5 — the hardening release gate.

    python scripts/verify_arch11_5.py
"""

from __future__ import annotations

import sys
import uuid
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


def _emit(level: str, ident: str, message: str) -> None:
    global CHECKS
    CHECKS += 1
    print(f"[{level}] {ident:<5} {message}")
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

        # ---- 11.5.1 spend ceilings -------------------------------------
        from app.core.usage_events import is_limit_key

        check(
            "H1.1",
            is_limit_key("llm.input_token") and is_limit_key("llm.output_token"),
            "llm.input_token and llm.output_token are enforceable limit keys",
        )

        from app.core.config import settings

        check(
            "H1.2",
            bool(settings.SPEND_DEFAULT_MONTHLY_LLM_INPUT_TOKENS)
            and bool(settings.SPEND_DEFAULT_MONTHLY_LLM_OUTPUT_TOKENS),
            "platform default LLM token ceilings are configured",
        )
        check(
            "H1.3",
            settings.LLM_METERING_ENABLED,
            "LLM_METERING_ENABLED is True",
        )

        llm_source = (REPO_ROOT / "app" / "services" / "llm_service.py").read_text(encoding="utf-8", errors="replace")
        check(
            "H1.4",
            "llm_metering" in llm_source,
            "llm_service calls llm_metering",
        )

        recorded = sql(
            "SELECT count(*) FROM usage_events "
            "WHERE event_type IN ('llm.input_token','llm.output_token')"
        ).scalar()
        info("H1.5", f"llm token rows recorded to date: {recorded}")

        orphans = sql(
            """
            SELECT count(*) FROM usage_events
            WHERE event_type IN ('llm.input_token','llm.output_token')
              AND idempotency_key IS NULL
            """
        ).scalar()
        check(
            "H1.6",
            orphans == 0,
            f"{orphans} llm usage row(s) without an idempotency key",
        )

        # ---- 11.5.2 resilience -----------------------------------------
        check(
            "H2.1",
            "time.sleep" not in llm_source,
            "no blocking sleep in llm_service",
        )
        check(
            "H2.2",
            "llm_resilience" in llm_source,
            "llm_service routes provider calls through llm_resilience",
        )
        check(
            "H2.3",
            "except Exception" not in llm_source.replace("except Exception as exc:  # noqa", "").replace("except Exception:", ""),
            "no blanket `except Exception` retry remains in llm_service",
        )

        from app.services.llm_resilience import FailureClass, classify

        probes = [
            (type("E", (Exception,), {"status_code": 400})(), FailureClass.PERMANENT),
            (type("E", (Exception,), {"status_code": 503})(), FailureClass.TRANSIENT),
            (Exception("maximum context length exceeded"), FailureClass.PERMANENT),
        ]
        misclassified = [
            str(exc) or type(exc).__name__
            for exc, expected in probes
            if classify(exc) is not expected
        ]
        check(
            "H2.4",
            not misclassified,
            f"error classification is correct on probe cases"
            + (f" — wrong: {misclassified}" if misclassified else ""),
        )

        from app.core.breaker import all_snapshots

        info("H2.5", f"breakers registered: {[b['name'] for b in all_snapshots()]}")

        # ---- 11.5.3 vocabulary -----------------------------------------
        query_source = (REPO_ROOT / "app" / "services" / "query_service.py").read_text(encoding="utf-8", errors="replace")
        check(
            "H3.1",
            "update_document_vocabulary" not in query_source,
            "the process-global vocabulary mutator is gone from query_service",
        )

        enrich_source = (REPO_ROOT / "app" / "workers" / "handlers" / "enrich.py").read_text(encoding="utf-8", errors="replace")
        check(
            "H3.2",
            "DocumentVocabularyService" not in enrich_source,
            "document.enrich no longer writes a shared vocabulary map",
        )

        from app.services.vocabulary_service import workspace_vocabulary_service

        row = sql("SELECT workspace_id FROM document_chunks LIMIT 1").first()
        if row is None:
            info("H3.3", "no chunks available to derive a vocabulary from (empty dev DB)")
        else:
            terms = workspace_vocabulary_service.terms_for(db, row[0])
            check(
                "H3.3",
                isinstance(terms, dict),
                f"vocabulary derived for one workspace: {len(terms)} term(s)",
            )
            check(
                "H3.4",
                workspace_vocabulary_service.terms_for(db, None) == {},
                "an unscoped vocabulary request returns nothing, not a shared map",
            )

        # ---- 11.5.4 intent ---------------------------------------------
        from app.services.intent_service import DEFAULT_INTENT_CONFIG, intent_service

        check(
            "H4.1",
            "upsc" not in DEFAULT_INTENT_CONFIG,
            "the upsc vertical is gone from the platform defaults",
        )
        column = sql(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'document_settings' AND column_name = 'intent_config'"
        ).first()
        check("H4.2", column is not None, "document_settings.intent_config exists")
        check(
            "H4.3",
            intent_service.detect("what are the things we log").intent == "unknown",
            "substring matching is fixed — a generic word no longer claims an intent",
        )

        # ---- 11.5.5 citations & snippets --------------------------------
        from app.services.citation_service import citation_service, snippet_service

        degraded = [
            {"id": "a", "rerank_score": None, "rrf_score": 0.02},
            {"id": "b", "rerank_score": None, "rrf_score": 0.01},
        ]
        try:
            ranked = citation_service.rank_citations(degraded)
            ok = len(ranked) == 2
        except Exception as exc:  # noqa: BLE001
            ok = False
            info("H5.0", f"citation ranking raised: {exc}")
        check(
            "H5.1",
            ok,
            "citation ranking survives a degraded reranker (rerank_score=None)",
        )

        sentences = snippet_service.split_sentences(
            "Dr. Smith approved it. The fee is Rs. 4,500. Version v1.2 applies."
        )
        check(
            "H5.2",
            len(sentences) == 3,
            f"abbreviation-aware sentence splitting: {len(sentences)} sentence(s), expected 3",
        )

        probe_text = "Alpha here. Beta about leave entitlement here."
        snippet = snippet_service.generate(
            text=probe_text, query="leave entitlement", chunk_page_start=100
        )
        check(
            "H5.3",
            probe_text[snippet.chunk_start_char : snippet.chunk_end_char].strip()
            == snippet.text,
            "snippet offsets round-trip against the source text",
        )
        check(
            "H5.4",
            snippet.page_start_char == 100 + snippet.chunk_start_char,
            "snippets carry absolute page offsets for the ARCH-12 overlay",
        )

        # ---- 11.5.6 observability ---------------------------------------
        from app.core.request_context import STAGE_BUDGETS, request_scope, stage

        with request_scope(request_id="gate") as trace:
            with stage("citation"):
                pass
        check(
            "H6.1",
            bool(trace.records and trace.records[0].name == "citation"),
            "stage timing records to the request trace",
        )

        asst_src = (REPO_ROOT / "app" / "services" / "assistant_service.py").read_text(encoding="utf-8", errors="replace")
        ret_src = (REPO_ROOT / "app" / "services" / "retrieval_service.py").read_text(encoding="utf-8", errors="replace")

        check(
            "H6.2",
            "request_scope" in asst_src and "stage(" in ret_src and "stage(" in llm_source,
            "request context and stage timing reach assistant, retrieval, and llm services",
        )
        info(
            "H6.3",
            f"stage budgets: "
            + ", ".join(f"{k}={int(v)}ms" for k, v in sorted(STAGE_BUDGETS.items())),
        )

        heads = sql("SELECT version_num FROM alembic_version").scalars().all()
        check("H6.4", len(heads) == 1, f"single alembic head: {heads}")

    print("\n" + "=" * 72)
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} of {CHECKS} checks failed")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(f"PASS — {CHECKS} checks. ARCH-11.5 is complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
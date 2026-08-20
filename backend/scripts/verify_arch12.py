#!/usr/bin/env python
"""ARCH-12 release gate.

Checks the things a test suite cannot: that the migration chain is at the
right head, that the columns exist in the *database* rather than only in the
models, that no code path bypasses the boundaries this phase built, and that
the operational queries the phase depends on are index-backed.

Follows the ARCH-08/09/10 verify script convention: every check prints one
line, failures are collected rather than raised, and the exit code is the
number of failures.

    python scripts/verify_arch12.py
    python scripts/verify_arch12.py --skip-db     # static checks only
"""

from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import pathlib
import sys
from typing import Callable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

FAILURES: list[str] = []
CHECKS_RUN = 0

REPO = pathlib.Path(__file__).resolve().parents[1]
APP = REPO / "app"

EXPECTED_HEAD = "arch12_step7_notification_deliveries"


def check(name: str) -> Callable:
    def decorator(fn: Callable[[], None]) -> Callable[[], None]:
        def wrapper() -> None:
            global CHECKS_RUN
            CHECKS_RUN += 1
            try:
                fn()
            except AssertionError as exc:
                FAILURES.append(f"{name}: {exc}")
                print(f"{RED}FAIL{RESET}  {name}\n        {exc}")
            except Exception as exc:  # noqa: BLE001
                FAILURES.append(f"{name}: {type(exc).__name__}: {exc}")
                print(f"{RED}ERROR{RESET} {name}\n        {type(exc).__name__}: {exc}")
            else:
                print(f"{GREEN}ok{RESET}    {name}")

        wrapper.__name__ = fn.__name__
        return wrapper

    return decorator


# ===========================================================================
# Static — Step 1: the settlement discipline
# ===========================================================================


@check("12.1  settlement runs on its own session, not the request's")
def _own_session() -> None:
    source = (APP / "services" / "stream_session.py").read_text(encoding="utf-8")
    assert "SessionLocal()" in source, "no independent session is opened"
    assert "def settlement_session" in source


@check("12.1  the settlement path contains no await points")
def _no_awaits_in_settlement() -> None:
    """An await in the finally is cancelled the instant a client disconnects.

    This is the single most important static check in the file: the code can
    look correct, pass every happy-path test, and silently fail to bill on
    exactly the path Step 1 exists to handle.
    """
    module = APP / "services" / "stream_session.py"
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in (
            "settle_and_persist",
            "settlement_session",
        ):
            raise AssertionError(f"{node.name} is async; it must be synchronous")
        if isinstance(node, ast.FunctionDef) and node.name == "settle_and_persist":
            for inner in ast.walk(node):
                if isinstance(inner, (ast.Await, ast.AsyncWith, ast.AsyncFor)):
                    raise AssertionError(
                        "settle_and_persist contains an await; cancellation "
                        "will interrupt it and the generation goes unbilled"
                    )


@check("12.1  settle_and_persist cannot raise out of a finally block")
def _never_raises() -> None:
    source = inspect.getsource(
        importlib.import_module("app.services.stream_session").settle_and_persist
    )
    assert "except Exception" in source, "no catch-all around the settlement"
    assert "return outcome" in source


@check("12.1  llm_metering.settle accepts estimated=")
def _settle_estimated() -> None:
    from app.services import llm_metering

    signature = inspect.signature(llm_metering.settle)
    assert "estimated" in signature.parameters, (
        "settle() has no estimated flag; a stream abandoned before its usage "
        "chunk would be settled as though the counts were provider facts"
    )


@check("12.1  the streaming generator settles in a finally")
def _generator_finally() -> None:
    module = APP / "services" / "assistant_stream.py"
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and node.finalbody:
            body = ast.dump(ast.Module(body=node.finalbody, type_ignores=[]))
            if "settle_and_persist" in body:
                found = True
    assert found, "no finally block calls settle_and_persist"


@check("12.1  CancelledError is re-raised, not swallowed")
def _cancel_propagates() -> None:
    source = (APP / "services" / "assistant_stream.py").read_text(encoding="utf-8")
    assert "except asyncio.CancelledError:" in source
    index = source.index("except asyncio.CancelledError:")
    tail = source[index : index + 400]
    assert "raise" in tail, "CancelledError is caught without re-raising"


# ===========================================================================
# Static — Step 2: refusal semantics
# ===========================================================================


@check("12.2  concurrency limits match A2 (2 per user, 10/min per conversation)")
def _limits() -> None:
    from app.core.config import settings

    assert settings.STREAM_MAX_CONCURRENT_PER_USER == 2
    assert settings.STREAM_MAX_MESSAGES_PER_MINUTE_PER_CONVERSATION == 10
    assert settings.STREAM_MAX_CONCURRENT_PER_ORG >= 2


@check("12.2  rate refusals are 429 and quota refusals are 402")
def _refusal_codes() -> None:
    from app.core.exception_handlers import resolve_exception_mapping
    from app.core.exceptions import RateLimitExceededError, SpendLimitExceededError

    assert resolve_exception_mapping(RateLimitExceededError())[0] == 429
    assert resolve_exception_mapping(SpendLimitExceededError(
        limit_key="k", period="p", dimension="d", ceiling="c", current="u", requested="r"
    ))[0] == 402


@check("12.2  the concurrency slot wraps the generator, not the request")
def _slot_scope() -> None:
    source = (APP / "api" / "v1" / "assistant_stream.py").read_text(encoding="utf-8")
    assert "with generation_slot(" in source
    slot_at = source.index("with generation_slot(")
    stream_at = source.index("assistant_stream_service.stream_answer")
    assert slot_at < stream_at, (
        "the slot must be acquired before the generator runs and held for its "
        "lifetime; a Depends releases at response time, which for streaming "
        "is before the first token"
    )


# ===========================================================================
# Static — Step 3: the budget
# ===========================================================================


@check("12.3  window shares sum to 1.0")
def _shares() -> None:
    from app.services.context_budget import (
        CONTEXT_SHARE,
        HEADROOM_SHARE,
        HISTORY_SHARE,
    )

    total = CONTEXT_SHARE + HISTORY_SHARE + HEADROOM_SHARE
    assert abs(total - 1.0) < 1e-9, f"shares sum to {total}"
    assert CONTEXT_SHARE == 0.60 and HISTORY_SHARE == 0.30


@check("12.3  the rolling digest is metered")
def _digest_metered() -> None:
    from app.services import llm_metering

    assert hasattr(llm_metering, "reserve_for_summary"), (
        "reserve_for_summary is missing; the digest is the second place "
        "generation happens with nobody watching and it must be budgeted"
    )
    source = inspect.getsource(llm_metering.reserve_for_summary)
    assert ":summary:" in source


@check("12.3  the streaming prompt no longer post-truncates context")
def _no_post_truncation() -> None:
    from app.services.llm_service import llm_service

    assert hasattr(llm_service, "build_streaming_prompt"), "patch 2 not applied"
    source = inspect.getsource(llm_service.build_streaming_prompt)
    assert "RAG_MAX_CONTEXT_LENGTH" not in source, (
        "context is being re-truncated after budgeting, which silently undoes "
        "the allocation — this is the A3 bug"
    )


@check("12.3  history is loaded without a message-count cap")
def _uncapped_history() -> None:
    from app.services.assistant_stream import AssistantStreamService

    source = inspect.getsource(AssistantStreamService._load_history)
    assert "MAX_CONVERSATION_MESSAGES" not in source, (
        "history is still capped by count; the window is bounded in tokens"
    )


# ===========================================================================
# Static — Step 4: the boundary and the filter
# ===========================================================================


@check("12.4  FencedContext does not subclass str and does not leak via str()")
def _fence_shape() -> None:
    from app.services.fenced_context import FencedContext

    assert not issubclass(FencedContext, str)
    fenced = FencedContext(
        _payload="SECRET-PAYLOAD",
        fence_nonce="n",
        passages_included=1,
        passages_dropped=0,
        truncated=False,
    )
    assert "SECRET-PAYLOAD" not in str(fenced)
    assert "SECRET-PAYLOAD" not in f"{fenced}"
    assert "SECRET-PAYLOAD" not in repr(fenced)


@check("12.4  every registered tool selector honours R33")
def _tool_boundary() -> None:
    from app.services.fenced_context import assert_tool_boundary

    assert_tool_boundary()


@check("12.4  nothing under app/services/tools imports the fence or retrieval")
def _tools_clean() -> None:
    tools = APP / "services" / "tools"
    assert tools.is_dir(), "app/services/tools/ is missing"

    forbidden = {
        "app.services.fenced_context",
        "app.services.retrieval_service",
        "app.services.chunk_retrieval_service",
        "app.services.hybrid_search_service",
        "app.services.context_assembly_service",
    }
    offenders: list[str] = []

    for path in sorted(tools.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "") in forbidden:
                offenders.append(f"{path.name} -> {node.module}")
            if isinstance(node, ast.Import):
                offenders += [
                    f"{path.name} -> {alias.name}"
                    for alias in node.names
                    if alias.name in forbidden
                ]

    assert not offenders, f"R33 violation: {offenders}"


@check("12.4  the output filter buffers wider than its longest pattern")
def _filter_window() -> None:
    from app.services.output_filter import LOOKBEHIND, StreamRedactor

    assert LOOKBEHIND >= 68, "the lookbehind is short enough to be bypassed"

    redactor = StreamRedactor()
    out = "".join(
        redactor.feed(part)
        for part in ["411", "1 1111", " 1111 11", "11", " " * 200]
    ) + redactor.flush()
    assert "4111" not in out, "a card split across tokens survived the filter"


@check("12.4  notification content uses the same filter as the stream")
def _shared_filter() -> None:
    source = (
        APP / "services" / "notification" / "outbox_dispatcher.py"
    ).read_text(encoding="utf-8")
    assert "from app.services.output_filter import" in source
    assert "redact_text" in source


# ===========================================================================
# Static — Step 5: F7
# ===========================================================================


@check("12.5  paddle no longer emits blocks=[] for text-layer pages")
def _f7_applied() -> None:
    source = (APP / "services" / "ocr" / "paddle.py").read_text(encoding="utf-8")
    assert "extract_text_layer_page" in source, "F7 patch not applied to paddle.py"
    assert "boxed_text_layer_pages" in source, "the extraction log has no F7 counter"


@check("12.5  digital page text is the newline-join of its block texts")
def _f7_invariant() -> None:
    source = (APP / "services" / "ocr" / "pdf_text_layer.py").read_text(encoding="utf-8")
    assert '"\\n".join(block.text for block in blocks)' in source, (
        "the join that makes DocumentPage.block_spans() resolve has changed; "
        "boxes will be silently dropped at chunk time"
    )


@check("12.5  text-layer boxes are converted to top-left pixel space")
def _f7_coordinates() -> None:
    from app.services.ocr.pdf_text_layer import POINTS_PER_INCH

    assert POINTS_PER_INCH == 72.0
    source = (APP / "services" / "ocr" / "pdf_text_layer.py").read_text(encoding="utf-8")
    assert "page_height_pt - top" in source, (
        "no y-axis inversion; boxes will be in PDF points bottom-left while "
        "every scanned page is in raster pixels top-left"
    )


# ===========================================================================
# Static — Step 6: provenance
# ===========================================================================


@check("12.6  the envelope carries bbox, char offsets, hash and audit id")
def _envelope_shape() -> None:
    from app.schemas.citation import CitationEnvelope, CitationSource

    source_fields = set(CitationSource.model_fields)
    for field in ("bbox", "page_start_char", "page_end_char", "chunk_id", "page_number"):
        assert field in source_fields, f"CitationSource is missing {field}"

    envelope_fields = set(CitationEnvelope.model_fields)
    for field in ("context_hash", "audit_log_id", "claims"):
        assert field in envelope_fields, f"CitationEnvelope is missing {field}"


@check("12.6  the seal is written before the first token")
def _seal_timing() -> None:
    from app.services.assistant_stream import AssistantStreamService

    prepare = inspect.getsource(AssistantStreamService.prepare)
    assert "seal_generation" in prepare, (
        "the generation is sealed inside the streaming loop rather than in "
        "prepare(); a stream abandoned at token 40 would then have no record "
        "of the context that produced those 40 tokens"
    )


@check("12.6  CONVERSATION and GENERATED exist in the audit enums")
def _audit_enums() -> None:
    from app.models.audit_log import AuditAction, AuditResourceType

    assert hasattr(AuditResourceType, "CONVERSATION")
    assert hasattr(AuditAction, "GENERATED")


# ===========================================================================
# Static — Step 7
# ===========================================================================


@check("12.7  notification.deliver is a registered job type")
def _job_registered() -> None:
    from app.workers.handlers import register_all
    from app.services import job_service

    register_all(replace=True)
    assert "notification.deliver" in job_service.JOB_HANDLERS


@check("12.7  delivery backoff is exponential and capped")
def _backoff() -> None:
    from app.models.notification_delivery import BACKOFF_CAP_SECONDS, backoff_delay

    delays = [backoff_delay(n).total_seconds() for n in range(12)]
    assert delays[1] > delays[0]
    assert max(delays) == BACKOFF_CAP_SECONDS
    assert delays[-1] == BACKOFF_CAP_SECONDS, "the schedule is not capped"


@check("12.7  DEAD is a status, not a derived comparison")
def _dead_status() -> None:
    from app.models.notification_delivery import NotificationDeliveryStatus

    assert NotificationDeliveryStatus.DEAD.value == "DEAD"


# ===========================================================================
# Database
# ===========================================================================


def _db_checks() -> None:
    from sqlalchemy import inspect as sa_inspect, text

    from app.db.session import engine

    @check("db    migration head is arch12_step7_notification_deliveries")
    def _head() -> None:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all()
        assert EXPECTED_HEAD in rows, f"head is {rows}, expected {EXPECTED_HEAD}"

    @check("db    conversation_messages has the streaming columns")
    def _stream_columns() -> None:
        columns = {
            column["name"]
            for column in sa_inspect(engine).get_columns("conversation_messages")
        }
        for name in (
            "stream_state",
            "truncated",
            "finish_reason",
            "usage_estimated",
            "stream_started_at",
            "context_hash",
            "audit_log_id",
        ):
            assert name in columns, f"missing column {name}"

    @check("db    stream_state / finish_reason CHECK constraints exist")
    def _stream_checks() -> None:
        with engine.connect() as connection:
            names = connection.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'conversation_messages'::regclass "
                    "AND contype = 'c'"
                )
            ).scalars().all()
        assert "ck_conversation_messages_finish_reason" in names
        assert "ck_conversation_messages_stream_state_reason" in names

    @check("db    the in-flight sweep is index-backed")
    def _in_flight_index() -> None:
        indexes = {
            index["name"]
            for index in sa_inspect(engine).get_indexes("conversation_messages")
        }
        assert "ix_conversation_messages_in_flight" in indexes

    @check("db    notification_deliveries exists with a partial due index")
    def _deliveries() -> None:
        inspector = sa_inspect(engine)
        assert "notification_deliveries" in inspector.get_table_names()
        indexes = {i["name"] for i in inspector.get_indexes("notification_deliveries")}
        assert "ix_notification_deliveries_due" in indexes
        assert "ix_notification_deliveries_dead" in indexes

    @check("db    no assistant row is stranded in STREAMING")
    def _no_stranded() -> None:
        from app.core.config import settings

        with engine.connect() as connection:
            stranded = connection.execute(
                text(
                    "SELECT count(*) FROM conversation_messages "
                    "WHERE stream_state = 'STREAMING' "
                    "AND stream_started_at < now() - make_interval(secs => :s)"
                ),
                {"s": float(settings.STREAM_DEADLINE_SECONDS)},
            ).scalar_one()
        assert stranded == 0, (
            f"{stranded} generations completed without settling — these are "
            "unbilled and the transcripts are incomplete"
        )

    @check("db    every settled stream has both usage rows")
    def _usage_pairs() -> None:
        with engine.connect() as connection:
            orphans = connection.execute(
                text(
                    """
                    SELECT count(*) FROM (
                        SELECT split_part(idempotency_key, ':', 1) || ':' ||
                               split_part(idempotency_key, ':', 2) || ':' ||
                               split_part(idempotency_key, ':', 3) AS scope,
                               count(*) AS rows
                        FROM usage_events
                        WHERE idempotency_key LIKE 'llm:%'
                          AND event_type IN ('llm.input_token', 'llm.output_token')
                        GROUP BY 1
                        HAVING count(*) <> 2
                    ) AS mismatched
                    """
                )
            ).scalar_one()
        assert orphans == 0, f"{orphans} metering scopes have an unpaired usage row"

    for fn in (
        _head,
        _stream_columns,
        _stream_checks,
        _in_flight_index,
        _deliveries,
        _no_stranded,
        _usage_pairs,
    ):
        fn()


# ===========================================================================


STATIC_CHECKS = [
    _own_session,
    _no_awaits_in_settlement,
    _never_raises,
    _settle_estimated,
    _generator_finally,
    _cancel_propagates,
    _limits,
    _refusal_codes,
    _slot_scope,
    _shares,
    _digest_metered,
    _no_post_truncation,
    _uncapped_history,
    _fence_shape,
    _tool_boundary,
    _tools_clean,
    _filter_window,
    _shared_filter,
    _f7_applied,
    _f7_invariant,
    _f7_coordinates,
    _envelope_shape,
    _seal_timing,
    _audit_enums,
    _job_registered,
    _backoff,
    _dead_status,
]


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-12 release gate")
    parser.add_argument("--skip-db", action="store_true")
    args = parser.parse_args()

    print(f"\n{YELLOW}ARCH-12 — AI Assistant & Stream Engine{RESET}")
    print("=" * 68)

    for fn in STATIC_CHECKS:
        fn()

    if not args.skip_db:
        print("-" * 68)
        try:
            _db_checks()
        except Exception as exc:  # noqa: BLE001
            FAILURES.append(f"database checks could not run: {exc}")
            print(f"{RED}ERROR{RESET} database checks: {exc}")

    print("=" * 68)
    if FAILURES:
        print(f"{RED}{len(FAILURES)} of {CHECKS_RUN} checks failed{RESET}\n")
        for failure in FAILURES:
            print(f"  - {failure}")
        print(
            f"\n{YELLOW}ARCH-12 is not releasable. Every failure above is a "
            f"path where a generation goes unbilled, unfiltered, or "
            f"unattributed.{RESET}\n"
        )
        return len(FAILURES)

    print(f"{GREEN}all {CHECKS_RUN} checks passed — ARCH-12 gate is green{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
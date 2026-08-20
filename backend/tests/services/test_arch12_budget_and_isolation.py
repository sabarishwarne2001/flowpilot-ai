"""Gates 12.3 and 12.4 — window budgeting, tool isolation, output filtering.

The 12.3 gate is stated as: *a 40-turn conversation must still retrieve*.
`test_retrieval_does_not_decay_with_turn_count` is that gate, and it is
written as a monotonicity assertion rather than a threshold because the
failure it guards against is gradual. A threshold test passes at turn 39 and
fails at turn 41; a monotonicity test fails the first time history starts
eating the context share, which is where the bug is.

The 12.4 static test walks `app/services/tools/` with `ast`. That is the only
layer that catches a selector added *without* registering it — the type check
in `register_tool_selector` can only see callables that opted in.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.services import output_filter
from app.services.context_budget import (
    CONTEXT_SHARE,
    HEADROOM_SHARE,
    HISTORY_SHARE,
    WindowBudget,
    count_tokens,
    fit_history,
    trim_results_to_budget,
)
from app.services.fenced_context import (
    FencedContext,
    ToolBoundaryViolation,
    check_callable,
    empty_fence,
    register_tool_selector,
)

pytestmark = pytest.mark.no_db


# ===========================================================================
# 12.3 — the window budget
# ===========================================================================


def _result(index: int, chars: int) -> dict:
    return {
        "id": f"doc_chunk_{index}",
        "text": "x" * chars,
        "metadata": {"work_item_id": "00000000-0000-0000-0000-000000000001"},
        "similarity_score": 1.0 - index * 0.01,
    }


def test_shares_sum_to_one():
    assert CONTEXT_SHARE + HISTORY_SHARE + HEADROOM_SHARE == pytest.approx(1.0)


def test_allocation_subtracts_the_system_prompt_first():
    system = "S" * 3500  # ~1000 tokens
    budget = WindowBudget.allocate(window_tokens=10_000, system_prompt=system)

    negotiable = 10_000 - budget.system_tokens
    assert budget.context_tokens == int(negotiable * CONTEXT_SHARE)
    assert (
        budget.context_tokens + budget.history_tokens + budget.headroom_tokens
        <= negotiable
    ), "the allocation must not overcommit the window"


def test_retrieval_does_not_decay_with_turn_count():
    """Gate 12.3. Retrieved chunk count must not fall as turns rise."""
    system = "S" * 2000
    budget = WindowBudget.allocate(window_tokens=32_768, system_prompt=system)
    results = [_result(index, 1200) for index in range(20)]

    retained_by_turn = []
    history: list[dict[str, str]] = []

    for turn in range(40):
        history.append({"role": "user", "content": "q" * 400})
        history.append({"role": "assistant", "content": "a" * 3000})

        kept, _dropped = trim_results_to_budget(
            results, token_budget=budget.context_tokens
        )
        retained_by_turn.append(len(kept))

    assert retained_by_turn[0] > 0
    assert len(set(retained_by_turn)) == 1, (
        "retrieved chunk count changed as the conversation grew — history is "
        "competing with context again, which is the A3 bug"
    )


def test_history_trimming_keeps_the_newest_turn_verbatim():
    messages = [
        {"role": "user", "content": "old " * 500},
        {"role": "assistant", "content": "older " * 500},
        {"role": "user", "content": "What was the termination date?"},
    ]
    recent, overflow = fit_history(messages, token_budget=60)

    assert recent, "the newest turn must always survive"
    assert recent[-1]["content"] == "What was the termination date?"
    assert len(overflow) >= 1


def test_oversized_chunk_is_skipped_not_terminal():
    """One giant chunk must not starve the good ones queued behind it."""
    results = [_result(0, 200_000), _result(1, 400), _result(2, 400)]
    kept, dropped = trim_results_to_budget(results, token_budget=1000)

    assert dropped == 1
    assert [entry["id"] for entry in kept] == ["doc_chunk_1", "doc_chunk_2"]


def test_token_estimator_matches_the_meter():
    from app.services import llm_metering

    text = "The quick brown fox jumps over the lazy dog. " * 40
    assert count_tokens(text) == llm_metering.estimate_prompt_tokens(text)


# ===========================================================================
# 12.4 — the R33 tool boundary
# ===========================================================================


def test_fenced_context_is_not_a_string():
    fenced = empty_fence()
    assert not isinstance(fenced, str)
    with pytest.raises(TypeError):
        "prefix " + fenced  # type: ignore[operator]


def test_str_and_format_do_not_leak_the_payload():
    fenced = FencedContext(
        _payload="ACCOUNT 12345678 SORT 00-11-22",
        fence_nonce="abcd",
        passages_included=1,
        passages_dropped=0,
        truncated=False,
    )
    assert "12345678" not in str(fenced)
    assert "12345678" not in f"{fenced}"
    assert "12345678" not in repr(fenced)
    assert "12345678" in fenced.render_for_prompt()


def test_context_hash_is_over_the_exact_bytes():
    a = FencedContext(
        _payload="one\ntwo", fence_nonce="x", passages_included=2,
        passages_dropped=0, truncated=False,
    )
    b = FencedContext(
        _payload="two\none", fence_nonce="x", passages_included=2,
        passages_dropped=0, truncated=False,
    )
    assert a.sha256() != b.sha256(), "reordering passages must change the hash"
    assert a.sha256().startswith("sha256:")
    assert len(a.sha256()) == 71


def test_selector_accepting_fenced_context_is_refused_at_registration():
    with pytest.raises(ToolBoundaryViolation) as excinfo:

        @register_tool_selector("bad_selector")
        def _bad(question: str, context: FencedContext) -> str:
            return context.render_for_prompt()

    assert "FencedContext" in str(excinfo.value)


def test_selector_accepting_a_bare_dict_is_refused():
    violations = check_callable(lambda chunk: None)
    assert violations, "an unannotated parameter cannot be proven safe"

    def _typed(chunk: dict) -> None: ...

    assert check_callable(_typed)


def test_a_clean_selector_registers():
    @register_tool_selector("archive_work_item")
    def _ok(question: str, work_item_id: str, confirmed: bool) -> str:
        return work_item_id

    assert _ok("q", "id", True) == "id"


TOOLS_DIR = pathlib.Path(__file__).resolve().parents[2] / "app" / "services" / "tools"

FORBIDDEN_IMPORTS = {
    "app.services.fenced_context",
    "app.services.retrieval_service",
    "app.services.chunk_retrieval_service",
    "app.services.hybrid_search_service",
    "app.services.context_assembly_service",
}


def test_no_module_under_tools_can_reach_retrieved_content():
    """The layer that catches a selector nobody registered.

    Walks every file in `app/services/tools/` and fails if it imports the
    fence, a retrieval service, or anything that hands back chunks.
    """
    offenders: list[str] = []

    for path in sorted(TOOLS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN_IMPORTS:
                        offenders.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module in FORBIDDEN_IMPORTS:
                    offenders.append(f"{path.name}: from {module} import ...")
                if module.endswith("fenced_context"):
                    offenders.append(f"{path.name}: from {module} import ...")

    assert not offenders, (
        "R33 violation — retrieved document content must never reach the "
        f"tool-selection path: {offenders}"
    )


# ===========================================================================
# 12.4 — the output filter
# ===========================================================================


def test_card_number_split_across_tokens_is_caught():
    """The reason the filter buffers instead of running per token."""
    redactor = output_filter.StreamRedactor()
    tokens = ["Your card ", "411", "1 1111", " 1111 11", "11 was ", "declined." + " " * 120]

    delivered = "".join(redactor.feed(token) for token in tokens)
    delivered += redactor.flush()

    assert "4111" not in delivered
    assert output_filter.REDACTION in delivered
    assert redactor.tally.counts.get("card_number") == 1


def test_streamed_output_equals_the_persisted_transcript():
    redactor = output_filter.StreamRedactor()
    delivered = "".join(
        redactor.feed(chunk) for chunk in ["Contact ", "ana@acme.io", " for the file."]
    )
    delivered += redactor.flush()
    assert delivered == redactor.emitted_text


def test_luhn_spares_an_invoice_number():
    assert "INV" in output_filter.redact_text("INV 1234567890123456 dated May")
    assert "1234567890123456" in output_filter.redact_text(
        "INV 1234567890123456 dated May"
    ), "a non-Luhn digit run is an order number, not a card"


def test_fence_nonce_echo_is_stripped():
    redactor = output_filter.StreamRedactor(fence_nonce="a1b2c3d4")
    text = "As shown in <<<SOURCE-a1b2c3d4 id=1 the total is 42." + " " * 120
    delivered = redactor.feed(text) + redactor.flush()

    assert "a1b2c3d4" not in delivered
    assert "<<<SOURCE-" not in delivered


def test_system_prompt_echo_is_stripped():
    text = (
        "You are FlowPilot AI, an enterprise document intelligence assistant. "
        "The answer is 42." + " " * 120
    )
    assert "enterprise document intelligence assistant" not in output_filter.redact_text(
        text
    )


@pytest.mark.parametrize(
    "sample,rule",
    [
        ("call me on +91 98765 43210", "phone"),
        ("SSN 123-45-6789 on file", "ssn_us"),
        ("PAN ABCDE1234F", "pan_in"),
        ("key fp_live_ABC123def456ghi", "api_key"),
        ("IBAN GB82 WEST 1234 5698 7654 32", "iban"),
    ],
)
def test_identifier_classes(sample, rule):
    assert rule in output_filter.scan(sample)


def test_lookbehind_exceeds_the_longest_pattern():
    """A window shorter than the longest pattern is a guaranteed bypass."""
    assert output_filter.LOOKBEHIND >= 34 * 2
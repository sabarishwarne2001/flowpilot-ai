"""ARCH-11 Step 1 — the golden set contract and the metrics computed from it.

No database. The point of these tests is that the *labels* behave correctly —
in particular that a span label survives re-chunking, which is the property the
whole Step 6 comparison rests on.
"""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest

from app.evaluation.golden_set import (
    GoldenSetError,
    SCHEMA_VERSION,
    load_golden_set,
    normalize,
    parse_golden_set,
    spans_covered,
    validate,
)
from app.evaluation.retrieval_metrics import aggregate, score_question

pytestmark = pytest.mark.no_db

EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "golden"
    / "arch11_golden_v1.example.json"
)


@pytest.fixture()
def payload() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _parse(payload: dict, *, strict_size: bool = False):
    return parse_golden_set(
        payload, path=EXAMPLE, sha256="0" * 64, strict_size=strict_size
    )


@pytest.fixture()
def golden(payload):
    # The example file holds four questions so the shape is readable; the size
    # contract is exercised separately in test_size_contract_is_enforced.
    return _parse(payload)


# ===========================================================================
# The contract
# ===========================================================================


def test_example_file_is_wellformed(golden):
    assert golden.version == SCHEMA_VERSION
    assert {w.alias for w in golden.workspaces} == {"engineering", "operations"}
    assert len(golden.documents) == 3
    assert golden.work_item_ids_for_workspace("operations") == [
        "55555555-5555-4555-8555-555555555555"
    ]


def test_size_contract_is_enforced(golden):
    with pytest.raises(GoldenSetError, match="40-60"):
        validate(golden, strict_size=True)


def test_question_without_spans_is_rejected(payload):
    payload = copy.deepcopy(payload)
    payload["questions"][0]["answer_spans"] = []
    with pytest.raises(GoldenSetError, match="answer_spans"):
        _parse(payload)


def test_span_shorter_than_twelve_characters_is_rejected(payload):
    payload = copy.deepcopy(payload)
    payload["questions"][0]["answer_spans"] = ["too short"]
    with pytest.raises(GoldenSetError, match="discriminating"):
        _parse(payload)


def test_relevant_document_in_another_workspace_is_rejected(payload):
    """An unsatisfiable label looks like a permanent retrieval failure, and
    somebody eventually 'fixes' it by loosening the tenancy filter."""
    payload = copy.deepcopy(payload)
    payload["questions"][0]["relevant_document_aliases"] = ["vendor_msa"]
    with pytest.raises(GoldenSetError, match="unsatisfiable"):
        _parse(payload)


def test_unknown_workspace_alias_is_rejected(payload):
    payload = copy.deepcopy(payload)
    payload["questions"][0]["workspace_alias"] = "finance"
    with pytest.raises(GoldenSetError, match="unknown workspace"):
        _parse(payload)


def test_version_is_not_widened(payload):
    payload = copy.deepcopy(payload)
    payload["version"] = "arch11-golden-v2"
    with pytest.raises(GoldenSetError, match="version"):
        _parse(payload)


def test_missing_file_says_how_to_build_one():
    with pytest.raises(GoldenSetError, match="build_golden_scaffold"):
        load_golden_set("evaluation/golden/does-not-exist.json")


# ===========================================================================
# Span matching
# ===========================================================================


def test_span_match_is_whitespace_and_case_insensitive():
    span = normalize("ninety (90) days prior written notice")
    chunk = "…may terminate\n  for convenience upon NINETY (90)\nDAYS PRIOR WRITTEN NOTICE."
    assert spans_covered([chunk], [span]) == {span}


def test_span_survives_rechunking():
    """The whole reason labels are spans and not chunk ids.

    Same passage, two different chunk boundaries and two different id schemes.
    The label matches both.
    """
    span = normalize("accrue 1.75 days of annual leave per completed calendar month")
    before = ["Full-time employees accrue 1.75 days of annual leave per completed "
              "calendar month of service."]
    after = ["…preamble text. Full-time employees accrue 1.75 days of annual leave "
             "per completed calendar month of service, pro-rated on joining."]
    assert spans_covered(before, [span]) == {span}
    assert spans_covered(after, [span]) == {span}


# ===========================================================================
# Metrics
# ===========================================================================


def _result(chunk_id: str, work_item_id: str, text: str) -> dict:
    return {
        "id": chunk_id,
        "text": text,
        "work_item_id": work_item_id,
        "metadata": {"work_item_id": work_item_id},
    }


def test_perfect_retrieval_scores_one(golden):
    question = golden.questions[0]
    doc = golden.document("leave_policy")
    results = [
        _result("c1", str(doc.work_item_id), question.answer_spans[0]),
    ]
    row = score_question(question, results, golden=golden, latency_ms=12.0)
    assert row.span_recall == 1.0
    assert row.mrr == 1.0
    assert row.chunk_precision == 1.0
    assert row.contamination == 0.0
    assert row.cross_tenant_hits == 0


def test_mrr_reflects_position(golden):
    question = golden.questions[0]
    doc = golden.document("leave_policy")
    results = [
        _result("c0", str(doc.work_item_id), "unrelated preamble"),
        _result("c1", str(doc.work_item_id), question.answer_spans[0]),
    ]
    row = score_question(question, results, golden=golden, latency_ms=1.0)
    assert row.mrr == 0.5
    assert row.chunk_precision == 0.5
    assert row.span_recall == 1.0


def test_foreign_workspace_chunk_is_counted_as_cross_tenant(golden):
    """The assertion that matters more than any quality number."""
    question = golden.questions[0]
    foreign = golden.document("vendor_msa")
    results = [_result("c9", str(foreign.work_item_id), "unrelated contract text")]
    row = score_question(question, results, golden=golden, latency_ms=1.0)
    assert row.cross_tenant_hits == 1
    assert row.contamination == 1.0
    assert row.span_recall == 0.0


def test_zero_results_are_visible_rather_than_silent(golden):
    question = golden.questions[0]
    row = score_question(question, [], golden=golden, latency_ms=1.0)
    assert row.retrieved == 0
    assert row.span_recall == 0.0
    summary = aggregate([row])
    assert summary["zero_result_questions"] == 1


def test_aggregate_reports_nearest_rank_p95(golden):
    question = golden.questions[0]
    rows = [
        score_question(question, [], golden=golden, latency_ms=float(ms))
        for ms in range(1, 21)
    ]
    summary = aggregate(rows)
    assert summary["latency_ms"]["p95"] == 19.0
    assert summary["latency_ms"]["max"] == 20.0


def test_errors_are_carried_not_swallowed(golden):
    question = golden.questions[0]
    row = score_question(
        question, [], golden=golden, latency_ms=1.0, error="RuntimeError: boom"
    )
    summary = aggregate([row])
    assert summary["errors"] == 1
    assert summary["scored"] == 0
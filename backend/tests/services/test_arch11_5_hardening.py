"""ARCH-11.5 — the hardening test suite."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.breaker import BreakerOpen, BreakerState, CircuitBreaker, get_breaker
from app.core.exceptions import SpendLimitExceededError
from app.core.request_context import (
    STAGE_BUDGETS,
    current_trace,
    get_request_id,
    request_scope,
    stage,
)
from app.models.spend_limit import SpendLimitPeriod
from app.models.usage_event import UsageEvent
from app.services import llm_metering, llm_resilience
from app.services import spend_control_service as spend
from app.services.citation_service import citation_service, snippet_service
from app.services.intent_service import UNKNOWN, intent_service


@pytest.fixture(autouse=True)
def _reset_breakers():
    for name in ("llm:groq", "llm:gemini", "reranker"):
        get_breaker(name).reset()
    yield
    for name in ("llm:groq", "llm:gemini", "reranker"):
        get_breaker(name).reset()


@pytest.fixture()
def org_id(db_session: Session) -> uuid.UUID:
    from app.models.organization import Organization, OrganizationStatus

    org = Organization(
        name="hardening",
        slug=f"h-{uuid.uuid4().hex[:10]}",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    db_session.flush([org])
    return org.id


@pytest.fixture()
def ai_settings() -> SimpleNamespace:
    return SimpleNamespace(
        provider=SimpleNamespace(value="groq"),
        model="llama-3.3-70b",
        max_output_tokens=1000,
        input_cost_per_1k_tokens=0.05,
        output_cost_per_1k_tokens=0.08,
        temperature=0.2,
    )


# ===========================================================================
# 11.5.1 — LLM spend ceilings
# ===========================================================================


def test_reservation_records_nothing(db_session, org_id, ai_settings):
    llm_metering.reserve(
        db_session,
        organization_id=org_id,
        workspace_id=None,
        conversation_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        prompt="hello " * 100,
        ai_settings=ai_settings,
    )
    rows = db_session.execute(
        select(UsageEvent).where(UsageEvent.organization_id == org_id)
    ).scalars().all()
    assert rows == []


def test_settle_records_the_providers_counts_not_the_estimate(
    db_session, org_id, ai_settings
):
    conversation_id, message_id = uuid.uuid4(), uuid.uuid4()
    reservation = llm_metering.reserve(
        db_session,
        organization_id=org_id,
        workspace_id=None,
        conversation_id=conversation_id,
        message_id=message_id,
        prompt="x" * 3500,
        ai_settings=ai_settings,
    )
    usage = SimpleNamespace(
        provider="groq", model="llama-3.3-70b",
        prompt_tokens=812, completion_tokens=310, total_tokens=1122,
    )
    llm_metering.settle(db_session, reservation=reservation, token_usage=usage)

    rows = llm_metering.recorded_for_message(
        db_session, organization_id=org_id,
        conversation_id=conversation_id, message_id=message_id,
    )
    by_type = {row.event_type: row for row in rows}
    assert by_type["llm.input_token"].quantity == Decimal(812)
    assert by_type["llm.output_token"].quantity == Decimal(310)
    assert "estimate_drift_tokens" in by_type["llm.input_token"].details


def test_output_ceiling_is_checked_against_the_worst_case(
    db_session, org_id, ai_settings
):
    spend.set_limit(
        db_session,
        organization_id=org_id,
        limit_key="llm.output_token",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("400"),
        hard_stop=True,
    )
    with pytest.raises(SpendLimitExceededError) as excinfo:
        llm_metering.reserve(
            db_session,
            organization_id=org_id,
            workspace_id=None,
            conversation_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            prompt="short prompt",
            ai_settings=ai_settings,
        )
    assert excinfo.value.limit_key == "llm.output_token"


def test_input_ceiling_refuses_before_the_call(db_session, org_id, ai_settings):
    spend.set_limit(
        db_session,
        organization_id=org_id,
        limit_key="llm.input_token",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("10"),
        hard_stop=True,
    )
    with pytest.raises(SpendLimitExceededError):
        llm_metering.reserve(
            db_session,
            organization_id=org_id,
            workspace_id=None,
            conversation_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            prompt="x" * 5000,
            ai_settings=ai_settings,
        )


def test_settling_twice_does_not_double_bill(db_session, org_id, ai_settings):
    conversation_id, message_id = uuid.uuid4(), uuid.uuid4()
    usage = SimpleNamespace(
        provider="groq", model="m", prompt_tokens=100,
        completion_tokens=50, total_tokens=150,
    )
    for _ in range(2):
        reservation = llm_metering.reserve(
            db_session,
            organization_id=org_id, workspace_id=None,
            conversation_id=conversation_id, message_id=message_id,
            prompt="hello", ai_settings=ai_settings,
        )
        llm_metering.settle(db_session, reservation=reservation, token_usage=usage)

    rows = llm_metering.recorded_for_message(
        db_session, organization_id=org_id,
        conversation_id=conversation_id, message_id=message_id,
    )
    assert len(rows) == 2


def test_missing_output_ceiling_is_refused(db_session, org_id, ai_settings):
    ai_settings.max_output_tokens = 0
    with pytest.raises(llm_metering.LLMMeteringError, match="max_output_tokens"):
        llm_metering.reserve(
            db_session,
            organization_id=org_id, workspace_id=None,
            conversation_id=uuid.uuid4(), message_id=uuid.uuid4(),
            prompt="hi", ai_settings=ai_settings,
        )


@pytest.mark.no_db
def test_token_estimate_errs_high():
    text = "the quick brown fox jumps over the lazy dog " * 10
    estimate = llm_metering.estimate_prompt_tokens(text)
    assert estimate > len(text.split())


# ===========================================================================
# 11.5.2 — resilience
# ===========================================================================


class _Permanent(Exception):
    status_code = 400


class _Transient(Exception):
    status_code = 503


class _RateLimitError(Exception):
    pass


@pytest.mark.no_db
@pytest.mark.parametrize(
    "exc,expected",
    [
        (_Permanent(), llm_resilience.FailureClass.PERMANENT),
        (_Transient(), llm_resilience.FailureClass.TRANSIENT),
        (_RateLimitError(), llm_resilience.FailureClass.RATE_LIMITED),
        (Exception("maximum context length exceeded"), llm_resilience.FailureClass.PERMANENT),
        (Exception("invalid_api_key"), llm_resilience.FailureClass.PERMANENT),
        (Exception("connection reset"), llm_resilience.FailureClass.TRANSIENT),
        (
            SpendLimitExceededError(
                limit_key="llm.output_token",
                period="MONTH",
                dimension="quantity",
                ceiling="10",
                current="0",
                requested="15",
            ),
            llm_resilience.FailureClass.REFUSED,
        ),
    ],
)
def test_classification(exc, expected):
    assert llm_resilience.classify(exc) is expected


@pytest.mark.no_db
def test_permanent_failure_is_not_retried():
    calls, slept = [], []
    def call(provider):
        calls.append(provider)
        raise _Permanent()

    with pytest.raises(llm_resilience.LLMPermanentError):
        llm_resilience.execute(call, provider="groq", sleep=slept.append)
    assert len(calls) == 1
    assert slept == []


@pytest.mark.no_db
def test_transient_failure_is_retried_without_blocking():
    slept = []
    attempts = []
    def call(provider):
        attempts.append(provider)
        if len(attempts) < 3:
            raise _Transient()
        return "ok"

    outcome = llm_resilience.execute(call, provider="groq", sleep=slept.append)
    assert outcome.value == "ok"
    assert len(attempts) == 3
    assert slept
    assert all(delay >= 0 for delay in slept)


@pytest.mark.no_db
def test_backoff_is_jittered_and_capped():
    delays = [
        llm_resilience.backoff_delay(attempt, base=0.5, cap=4.0, rate_limited=False)
        for attempt in range(1, 6)
        for _ in range(20)
    ]
    assert max(delays) <= 4.0
    assert len(set(round(d, 4) for d in delays)) > 5


@pytest.mark.no_db
def test_deadline_bounds_the_whole_operation(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_REQUEST_DEADLINE_SECONDS", 0.0)
    with pytest.raises(llm_resilience.LLMUnavailable):
        llm_resilience.execute(
            lambda provider: (_ for _ in ()).throw(_Transient()),
            provider="groq",
            sleep=lambda _: None,
        )


@pytest.mark.no_db
def test_spend_refusal_never_fails_over(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_FAILOVER_ENABLED", True)
    seen = []

    def call(provider):
        seen.append(provider)
        raise SpendLimitExceededError(
            limit_key="llm.output_token",
            period="MONTH",
            dimension="quantity",
            ceiling="10",
            current="0",
            requested="15",
        )

    with pytest.raises(SpendLimitExceededError):
        llm_resilience.execute(
            call, provider="groq", fallback_provider="gemini", sleep=lambda _: None
        )
    assert seen == ["groq"]


@pytest.mark.no_db
def test_failover_is_recorded_not_hidden(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_FAILOVER_ENABLED", True)

    def call(provider):
        if provider == "groq":
            raise _Transient()
        return "answer"

    outcome = llm_resilience.execute(
        call, provider="groq", fallback_provider="gemini", sleep=lambda _: None
    )
    assert outcome.provider == "gemini"
    assert outcome.failed_over is True


@pytest.mark.no_db
def test_breaker_stops_hammering_a_degraded_provider(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_FAILOVER_ENABLED", False)
    calls = []

    def call(provider):
        calls.append(provider)
        raise _Transient()

    for _ in range(3):
        with pytest.raises(llm_resilience.LLMUnavailable):
            llm_resilience.execute(call, provider="groq", sleep=lambda _: None)

    before = len(calls)
    with pytest.raises(llm_resilience.LLMUnavailable):
        llm_resilience.execute(call, provider="groq", sleep=lambda _: None)
    assert len(calls) == before
    assert llm_resilience.provider_breaker("groq").state is BreakerState.OPEN


# ===========================================================================
# 11.5.3 — tenant-scoped vocabulary
# ===========================================================================


@pytest.fixture()
def two_corpora(db_session: Session):
    from app.models.document_chunk import EMBEDDING_DIMENSION, DocumentChunk
    from app.models.organization import Organization, OrganizationStatus
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace, WorkspaceStatus

    built = {}
    corpora = {
        "legal": [
            "The vendor shall indemnify the customer against third-party claims.",
            "Termination for convenience requires ninety days written notice.",
            "Governing law is the law of England and Wales, exclusively.",
            "The indemnity survives termination of this agreement.",
        ],
        "medical": [
            "Patients presenting with tachycardia require immediate triage.",
            "Administer the prescribed anticoagulant within thirty minutes.",
            "Document the triage outcome in the patient record.",
            "Anticoagulant dosing follows the weight-banded protocol.",
        ],
    }
    for label, corpus in corpora.items():
        org = Organization(
            name=label, slug=f"{label}-{uuid.uuid4().hex[:8]}",
            status=OrganizationStatus.ACTIVE,
        )
        db_session.add(org)
        db_session.flush([org])
        workspace = Workspace(
            organization_id=org.id, slug=f"{label}-ws",
            workspace_name=label, status=WorkspaceStatus.ACTIVE,
        )
        db_session.add(workspace)
        db_session.flush([workspace])
        item = WorkItem(
            workspace_id=workspace.id,
            original_filename=f"{label}.pdf",
            stored_filename=f"{org.id}/{uuid.uuid4()}.pdf",
            file_type="application/pdf",
            file_size=1024,
            extracted_text="\n\n".join(corpus),
        )
        db_session.add(item)
        db_session.flush([item])
        for index, content in enumerate(corpus):
            db_session.add(
                DocumentChunk(
                    workspace_id=workspace.id,
                    organization_id=org.id,
                    work_item_id=item.id,
                    chunk_index=index,
                    content=content,
                    token_count=len(content.split()),
                    embedding=[0.1] * EMBEDDING_DIMENSION,
                    embedding_model="sentence-transformers/all-MiniLM-L6-v2",
                )
            )
        db_session.flush()
        built[label] = workspace
    return built


def test_vocabulary_is_workspace_scoped(db_session, two_corpora):
    from app.services.vocabulary_service import workspace_vocabulary_service as vocab

    vocab.clear()
    legal = set(vocab.terms_for(db_session, two_corpora["legal"].id))
    medical = set(vocab.terms_for(db_session, two_corpora["medical"].id))

    assert legal and medical
    assert not any("indemn" in term for term in medical)
    assert not any("anticoagul" in term for term in legal)


def test_unscoped_call_gets_no_expansion(db_session):
    from app.services.vocabulary_service import workspace_vocabulary_service as vocab

    assert vocab.terms_for(db_session, None) == {}
    assert vocab.terms_for(None, uuid.uuid4()) == {}
    assert vocab.expand(None, workspace_id=None, query="indemnity") == []


def test_cold_cache_recomputes_rather_than_returning_empty(db_session, two_corpora):
    from app.services.vocabulary_service import workspace_vocabulary_service as vocab

    workspace_id = two_corpora["legal"].id
    vocab.clear()
    assert vocab.terms_for(db_session, workspace_id)
    vocab.invalidate(workspace_id)
    assert vocab.terms_for(db_session, workspace_id)


def test_empty_workspace_yields_no_terms(db_session):
    from app.models.organization import Organization, OrganizationStatus
    from app.models.workspace import Workspace, WorkspaceStatus
    from app.services.vocabulary_service import workspace_vocabulary_service as vocab

    org = Organization(
        name="empty", slug=f"e-{uuid.uuid4().hex[:8]}",
        status=OrganizationStatus.ACTIVE,
    )
    db_session.add(org)
    db_session.flush([org])
    workspace = Workspace(
        organization_id=org.id, slug="empty-ws", workspace_name="Empty",
        status=WorkspaceStatus.ACTIVE,
    )
    db_session.add(workspace)
    db_session.flush([workspace])
    assert vocab.terms_for(db_session, workspace.id) == {}


# ===========================================================================
# 11.5.4 — intent
# ===========================================================================


@pytest.mark.no_db
def test_upsc_is_gone():
    from app.services.intent_service import DEFAULT_INTENT_CONFIG

    assert "upsc" not in DEFAULT_INTENT_CONFIG
    flattened = " ".join(
        keyword for keywords in DEFAULT_INTENT_CONFIG.values() for keyword in keywords
    )
    for legacy in ("prelims", "mains", "ias", "civil service"):
        assert legacy not in flattened


@pytest.mark.no_db
def test_word_boundaries_are_respected():
    assert intent_service.detect("what are the things we log").intent == UNKNOWN


@pytest.mark.no_db
def test_a_single_generic_keyword_is_not_an_intent():
    result = intent_service.detect("what is the total")
    assert result.intent == UNKNOWN
    assert result.confident is False


@pytest.mark.no_db
def test_a_clear_query_is_detected_confidently():
    result = intent_service.detect(
        "what are the payment terms and amount due on this invoice"
    )
    assert result.intent == "invoice"
    assert result.confident is True


@pytest.mark.no_db
def test_phrases_outweigh_single_terms():
    result = intent_service.detect("what is the governing law of this agreement")
    assert result.intent == "contract"


@pytest.mark.no_db
def test_malformed_config_falls_back_rather_than_raising():
    import app.services.intent_service as module

    original = module.IntentService._config_for
    try:
        module.IntentService._config_for = lambda self, db, ws: module.DEFAULT_INTENT_CONFIG
        assert intent_service.detect("invoice payment terms amount due").intent == "invoice"
    finally:
        module.IntentService._config_for = original


# ===========================================================================
# 11.5.5 — citations and snippets
# ===========================================================================


@pytest.mark.no_db
def test_none_rerank_score_does_not_crash():
    results = [
        {"id": "a", "rerank_score": None, "rrf_score": 0.02, "similarity_score": 0.5},
        {"id": "b", "rerank_score": None, "rrf_score": 0.01, "similarity_score": 0.9},
    ]
    ranked = citation_service.rank_citations(results)
    assert {row["id"] for row in ranked} == {"a", "b"}


@pytest.mark.no_db
def test_near_identical_scores_do_not_produce_a_manufactured_spread():
    results = [
        {"id": "a", "rrf_score": 0.9001, "similarity_score": 0.10},
        {"id": "b", "rrf_score": 0.9000, "similarity_score": 0.99},
        {"id": "c", "rrf_score": 0.8999, "similarity_score": 0.98},
    ]
    ranked = citation_service.rank_citations(results)
    assert ranked[0]["id"] != "a"


@pytest.mark.no_db
def test_a_missing_signal_is_not_evidence_against_a_result():
    results = [
        {"id": "a", "rrf_score": 0.02},
        {"id": "b", "rrf_score": 0.01, "rerank_score": 5.0},
    ]
    ranked = citation_service.rank_citations(results)
    assert ranked[0]["id"] == "b"
    assert len(ranked) == 2


@pytest.mark.no_db
def test_no_signals_preserves_retrieval_order():
    results = [{"id": "x"}, {"id": "y"}, {"id": "z"}]
    assert [r["id"] for r in citation_service.rank_citations(results)] == ["x", "y", "z"]


@pytest.mark.no_db
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Dr. Smith approved it. The fee applies.", 2),
        ("The fee is Rs. 4,500 per annum. See Fig. 3.", 2),
        ("Version v1.2 supersedes v1.1. It is final.", 2),
        ("Refer to No. 4 and No. 5. Both apply.", 2),
    ],
)
def test_abbreviations_do_not_end_sentences(text, expected):
    assert len(snippet_service.split_sentences(text)) == expected


@pytest.mark.no_db
def test_snippet_offsets_round_trip():
    text = (
        "Dr. Smith approved the request. The fee is Rs. 4,500 per annum. "
        "Employees accrue 1.75 days of leave monthly."
    )
    result = snippet_service.generate(text=text, query="leave accrual", max_characters=80)
    assert text[result.chunk_start_char : result.chunk_end_char].strip() == result.text


@pytest.mark.no_db
def test_snippet_carries_absolute_page_offsets():
    text = "Alpha sentence here. Beta sentence about leave entitlement here."
    result = snippet_service.generate(
        text=text, query="leave entitlement", chunk_page_start=5000
    )
    assert result.page_start_char == 5000 + result.chunk_start_char
    assert result.page_end_char == 5000 + result.chunk_end_char


@pytest.mark.no_db
def test_snippet_without_offsets_is_still_valid():
    result = snippet_service.generate(text="No sentence terminator here", query="x")
    assert result.text
    assert result.page_start_char is None


# ===========================================================================
# 11.5.6 — observability
# ===========================================================================


@pytest.mark.no_db
def test_request_id_is_visible_without_being_threaded():
    with request_scope(request_id="abc123") as trace:
        assert get_request_id() == "abc123"
        with stage("retrieval") as details:
            details["results"] = 7
        assert trace.records[0].name == "retrieval"
        assert trace.records[0].details["results"] == 7
    assert get_request_id() is None


@pytest.mark.no_db
def test_stage_records_a_failure_and_reraises():
    with request_scope() as trace:
        with pytest.raises(ValueError):
            with stage("rerank"):
                raise ValueError("boom")
        assert trace.records[0].error.startswith("ValueError")


@pytest.mark.no_db
def test_budget_breach_is_flagged():
    with request_scope() as trace:
        with stage("citation"):
            pass
        record = trace.records[0]
        assert record.budget_ms == STAGE_BUDGETS["citation"]
        assert record.over_budget is False


@pytest.mark.no_db
def test_trace_is_absent_outside_a_scope():
    assert current_trace() is None
    with stage("retrieval"):
        pass
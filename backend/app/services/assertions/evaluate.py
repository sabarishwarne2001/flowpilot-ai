"""ARCH-33 §4.5 — evaluation: parsers for typed families, a model for the rest.

TWO PATHS, ONE OUTPUT
=====================

`evaluate()` returns an `Evaluation` whatever family it ran. Downstream —
`triage.py`, the node executor, the review queue — never branches on family
again. That matters because the routing rule is the safety property, and a
routing rule with a per-family exception is a routing rule with a hole.

THE LLM PATH'S GUARD, AND WHY IT IS HERE AND NOT IN THE PROMPT
==============================================================

The model is asked for structured JSON containing a verdict and the exact
sentence it relied on. Asking is not enforcement: a model that invents a quote
produces the same JSON as a model that read one.

So the returned quote is verified against the retrieved chunks by
`quotecheck.verify_quote`, and an unverified quote forces confidence 0 and
routing to TRIAGE — never an automatic pass. §4.3: "This converts the most
common LLM failure — a confident answer about text that is not there — into a
routing decision instead of a wrong pass."

`features.raw_score` enforces the same rule a second time, arithmetically:
`quote_required and not quote_grounded` returns exactly 0.0 before any
weighting runs. Two independent expressions of one rule, because this is the
single guard on the only path in ARCH-33 where nothing deterministic checked
the answer.

NO NEW COST CATEGORY
====================

§4.2 and §4.7 are both explicit. The LLM family runs on `llm_service`'s
EXISTING routing — `resolve_routing` resolves the tenant's own BYOK route and
credentials, falls back to the platform key only where that tenant is entitled
to it, and is bounded by the spend limits already in force. The tokens are
emitted as `llm.input_token` and `llm.output_token`, the meters that already
exist. Nothing here introduces a provider, a price or a key.

WHAT IS PURE AND WHAT IS NOT
============================

`evaluate_deterministic()` and `score()` take chunks and a plan and hold no
Session — they are driven directly by `verify_arch33.py`. `evaluate()` holds
one, because the LLM route needs the tenant's settings and credentials.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.services.assertions import features as features_module
from app.services.assertions import quotecheck, vocabulary as vocab
from app.services.assertions.families import Chunk, FamilyReading, ReadingSet, read

logger = logging.getLogger("app.services.assertions.evaluate")

__all__ = [
    "Evaluation",
    "LLMAnswer",
    "build_llm_prompt",
    "parse_llm_answer",
    "evaluate_deterministic",
    "evaluate_llm",
    "evaluate",
    "score",
    "LLM_TASK_TYPE",
]

#: The assertion runs on the tenant's VERIFICATION route, not the assistant's.
#: A tenant that pinned a cheap model for chat and a careful one for
#: verification meant that distinction, and an assertion is verification work:
#: its output decides whether a document continues without a human.
LLM_TASK_TYPE: str = "VERIFICATION"

#: Low, and fixed. An assertion is not a creative task, and a temperature the
#: workspace happens to have set for chat would make the same document answer
#: differently on a re-run — which makes `input_digest` a lie.
LLM_TEMPERATURE: float = 0.0

#: Characters of retrieved text handed to the model. Bounded here rather than
#: trusted from settings, because the prompt is part of what the quote is
#: checked against and a truncated context would reject quotes that were
#: genuinely in the document.
MAX_CONTEXT_CHARS: int = 12_000


@dataclass(frozen=True)
class LLMAnswer:
    """What the model returned, before anything has been verified."""

    verdict: str
    quote: str
    value: Optional[str] = None
    reasoning: str = ""
    raw: str = ""


@dataclass(frozen=True)
class Evaluation:
    """One assertion, one document, one answer — family-independent."""

    family: str
    verdict: str
    raw_score: Decimal
    features: Any
    evidence: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    extracted_value: Optional[dict[str, Any]] = None
    best: Optional[FamilyReading] = None
    #: LLM mode only.
    quote_verdict: Optional[Any] = None
    token_usage: Optional[Any] = None
    #: Phrases from the retrieval query that literally occur in the quote the
    #: verdict rests on. `retrieve.record_hits` credits exactly these.
    matched_phrases: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def quote(self) -> str:
        if self.best is not None:
            return self.best.quote
        for item in self.evidence:
            value = item.get("quote")
            if value:
                return str(value)
        return ""

    def as_details(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "verdict": self.verdict,
            "raw_score": str(self.raw_score),
            "features": self.features.as_evidence() if self.features else None,
            "evidence_count": len(self.evidence),
        }


# ===========================================================================
# Scoring — pure
# ===========================================================================


def score(
    reading_set: Optional[ReadingSet],
    *,
    family: str,
    chunks: Sequence[Chunk],
    quote_required: bool = False,
    quote_grounded: bool = False,
    extraction_confidence: Optional[float] = None,
) -> tuple[Any, Decimal]:
    """`(FeatureVector, raw_score)`. Pure; gated offline."""
    retrieval_score = max((chunk.retrieval_score for chunk in chunks), default=0.0)
    vector = features_module.features_from(
        reading_set,
        family=family,
        retrieval_score=retrieval_score,
        chunk_count=len(chunks),
        quote_required=quote_required,
        quote_grounded=quote_grounded,
        extraction_confidence=extraction_confidence,
    )
    return vector, features_module.raw_score(vector)


def _matched_phrases(quote: str, phrases: Sequence[str]) -> tuple[str, ...]:
    """Query phrases that literally occur in the quote the verdict rests on."""
    haystack = quotecheck.normalize_for_quote(quote or "")
    if not haystack:
        return ()
    return tuple(
        phrase
        for phrase in phrases
        if phrase.strip()
        and quotecheck.normalize_for_quote(phrase) in haystack
    )


def _extracted_value(
    reading: Optional[FamilyReading], plan: Any
) -> Optional[dict[str, Any]]:
    """What goes in `assertion_evaluations.extracted_value` and, through
    triage, in `document_verification_fields.consensus_value`.

    A dict rather than a scalar, because the reviewer's screen needs the unit
    and the page as much as the number: "30" is not an answer a human can
    check and "30 days, page 4" is.
    """
    if reading is None:
        return None
    return {
        "value": None if reading.value is None else str(reading.value),
        "unit": reading.unit,
        "literal": reading.literal,
        "page_number": reading.page_number,
        "chunk_id": reading.chunk_id,
        "quote": reading.quote,
        "subject": getattr(plan, "subject", None),
    }


# ===========================================================================
# Deterministic families — pure
# ===========================================================================


def evaluate_deterministic(
    plan: Any,
    chunks: Sequence[Chunk],
    *,
    workspace_currency: Optional[str] = None,
    query_phrases: Sequence[str] = (),
) -> Evaluation:
    """Run the family's parser over the retrieved chunks. No Session."""
    reading_set = read(plan, chunks, workspace_currency=workspace_currency)
    vector, raw = score(
        reading_set, family=plan.family, chunks=chunks
    )
    best = reading_set.best
    return Evaluation(
        family=plan.family,
        verdict=reading_set.verdict,
        raw_score=raw,
        features=vector,
        evidence=tuple(reading_set.as_evidence()),
        extracted_value=_extracted_value(best, plan),
        best=best,
        matched_phrases=_matched_phrases(
            best.quote if best else "", query_phrases
        ),
    )


# ===========================================================================
# The LLM family
# ===========================================================================

_LLM_INSTRUCTIONS = """You are checking one requirement against one document.

REQUIREMENT
{sentence}

DOCUMENT EXCERPTS
{context}

Answer ONLY with a JSON object, no prose and no markdown fences:

{{"verdict": "PASS" | "FAIL" | "UNDETERMINED",
  "quote": "<the exact sentence from the excerpts you relied on>",
  "value": "<the value you read, or null>",
  "reasoning": "<one short sentence>"}}

Rules you must follow:
- "quote" must be copied character for character from the excerpts above. Do
  not paraphrase it, do not join two sentences, do not correct its spelling.
- If the excerpts do not settle the requirement, answer "UNDETERMINED" and set
  "quote" to "".
- Never answer "PASS" or "FAIL" without a quote from the excerpts."""


def build_llm_prompt(plan: Any, chunks: Sequence[Chunk]) -> str:
    """The prompt. Pure, so the gate can assert what it demands.

    The excerpts are numbered and bounded. The instruction to copy the quote
    character for character is not a politeness — `quotecheck.verify_quote`
    normalises whitespace and nothing else, so a model that paraphrases fails
    the check and its answer is triaged.
    """
    parts: list[str] = []
    used = 0
    for index, chunk in enumerate(chunks, start=1):
        body = chunk.text.strip()
        if not body:
            continue
        if used + len(body) > MAX_CONTEXT_CHARS:
            body = body[: max(0, MAX_CONTEXT_CHARS - used)]
        if not body:
            break
        used += len(body)
        page = f" (page {chunk.page_number})" if chunk.page_number else ""
        parts.append(f"[{index}]{page} {body}")
        if used >= MAX_CONTEXT_CHARS:
            break

    return _LLM_INSTRUCTIONS.format(
        sentence=(getattr(plan, "sentence", "") or "").strip(),
        context="\n\n".join(parts) if parts else "(no text was retrieved)",
    )


def parse_llm_answer(raw: str) -> LLMAnswer:
    """Read the model's JSON, refusing anything that is not an answer.

    A malformed response becomes UNDETERMINED with an empty quote rather than
    raising. The quote check then refuses it and it is triaged, which is the
    same destination a refusal would reach — with a row a reviewer can open
    instead of a failed node run they cannot.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return LLMAnswer(
            verdict=vocab.VERDICT_UNDETERMINED, quote="", raw=raw or ""
        )

    try:
        payload = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return LLMAnswer(
            verdict=vocab.VERDICT_UNDETERMINED, quote="", raw=raw or ""
        )

    verdict = str(payload.get("verdict") or "").strip().upper()
    if verdict not in vocab.VERDICTS:
        verdict = vocab.VERDICT_UNDETERMINED

    value = payload.get("value")
    return LLMAnswer(
        verdict=verdict,
        quote=str(payload.get("quote") or ""),
        value=None if value is None else str(value),
        reasoning=str(payload.get("reasoning") or ""),
        raw=raw or "",
    )


def evaluate_llm_answer(
    plan: Any,
    answer: LLMAnswer,
    chunks: Sequence[Chunk],
    *,
    query_phrases: Sequence[str] = (),
    token_usage: Any = None,
) -> Evaluation:
    """Verify the quote and score the answer. PURE — no model call in here.

    Split from `evaluate_llm` on purpose: the gate that must prove an
    unverified quote can never pass has to run without a provider, a key or a
    network, and this is the function it drives.
    """
    verdict_quote = quotecheck.verify_quote(answer.quote, chunks)

    verdict = answer.verdict
    notes: list[str] = []
    if not verdict_quote.grounded:
        # Never an automatic pass. §4.3. The verdict is downgraded as well as
        # the confidence, so that nothing downstream can read a PASS with a
        # zero score and decide the score was a bug.
        verdict = vocab.VERDICT_UNDETERMINED
        notes.append(verdict_quote.reason)

    vector, raw = score(
        None,
        family=vocab.FAMILY_LLM,
        chunks=chunks,
        quote_required=True,
        quote_grounded=verdict_quote.grounded,
        extraction_confidence=verdict_quote.confidence,
    )

    evidence: list[dict[str, Any]] = [
        {
            "chunk_id": verdict_quote.chunk_id,
            "chunk_index": verdict_quote.chunk_index,
            "page_number": verdict_quote.page_number,
            "quote": answer.quote,
            "span": None,
            "value": answer.value,
            "unit": None,
            "literal": None,
            "confidence": verdict_quote.confidence,
            "notes": [answer.reasoning] if answer.reasoning else [],
            "quote_check": verdict_quote.as_evidence(),
        }
    ]

    extracted = (
        {
            "value": answer.value,
            "unit": None,
            "literal": None,
            "page_number": verdict_quote.page_number,
            "chunk_id": verdict_quote.chunk_id,
            "quote": answer.quote,
            "subject": getattr(plan, "subject", None),
        }
        if verdict_quote.grounded
        else None
    )

    return Evaluation(
        family=vocab.FAMILY_LLM,
        verdict=verdict,
        raw_score=raw,
        features=vector,
        evidence=tuple(evidence),
        extracted_value=extracted,
        best=None,
        quote_verdict=verdict_quote,
        token_usage=token_usage,
        matched_phrases=(
            _matched_phrases(answer.quote, query_phrases)
            if verdict_quote.grounded
            else ()
        ),
        notes=tuple(notes),
    )


def evaluate_llm(
    db: Session,
    *,
    plan: Any,
    chunks: Sequence[Chunk],
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    query_phrases: Sequence[str] = (),
) -> Evaluation:
    """Run the tenant's EXISTING model route. No new cost category."""
    from app import crud
    from app.core import byok_providers
    from app.services.llm_service import llm_service

    ai_settings = crud.get_ai_settings(db, workspace_id=workspace_id)
    if ai_settings is None:
        # No configured model for this workspace. UNDETERMINED with an empty
        # quote, which triages — rather than an exception, which would fail
        # the node and strand the document with nothing for a reviewer.
        return evaluate_llm_answer(
            plan,
            LLMAnswer(
                verdict=vocab.VERDICT_UNDETERMINED,
                quote="",
                reasoning=(
                    "This workspace has no AI model configured, so an "
                    "AI-evaluated assertion cannot run."
                ),
            ),
            chunks,
            query_phrases=query_phrases,
        )

    effective, byok_client, credential_use = llm_service.resolve_routing(
        db=db,
        organization_id=organization_id,
        task_type=getattr(byok_providers, "TASK_VERIFICATION", LLM_TASK_TYPE),
        ai_settings=ai_settings,
    )

    prompt = build_llm_prompt(plan, chunks)

    try:
        raw, token_usage = llm_service._execute_query(  # noqa: SLF001
            prompt=prompt,
            temperature=LLM_TEMPERATURE,
            ai_settings=effective,
            byok_client=byok_client,
        )
    except Exception as exc:  # noqa: BLE001
        # Provider refusal, timeout, breaker open. All of these mean "no
        # answer", and "no answer" is UNDETERMINED and a review — not a failed
        # execution. The document still needs a decision from somebody.
        logger.warning(
            "assertion.llm_unavailable",
            extra={
                "workspace_id": str(workspace_id),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return evaluate_llm_answer(
            plan,
            LLMAnswer(
                verdict=vocab.VERDICT_UNDETERMINED,
                quote="",
                reasoning="The AI model could not be reached for this check.",
            ),
            chunks,
            query_phrases=query_phrases,
        )

    if credential_use is not None:
        # BYOK: the tenant's own key served this call, and ARCH-22 records the
        # use so the credential's last-used and rotation state stay accurate.
        try:
            credential_use.settle(db)
        except AttributeError:  # pragma: no cover - older credential shapes
            pass

    answer = parse_llm_answer(raw)
    return evaluate_llm_answer(
        plan,
        answer,
        chunks,
        query_phrases=query_phrases,
        token_usage=token_usage,
    )


# ===========================================================================
# The one entry point
# ===========================================================================


def evaluate(
    db: Session,
    *,
    plan: Any,
    chunks: Sequence[Chunk],
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workspace_currency: Optional[str] = None,
    query_phrases: Sequence[str] = (),
) -> Evaluation:
    """Deterministic or LLM, decided by the family and nothing else."""
    if plan.family == vocab.FAMILY_LLM:
        return evaluate_llm(
            db,
            plan=plan,
            chunks=chunks,
            organization_id=organization_id,
            workspace_id=workspace_id,
            query_phrases=query_phrases,
        )
    return evaluate_deterministic(
        plan,
        chunks,
        workspace_currency=workspace_currency,
        query_phrases=query_phrases,
    )
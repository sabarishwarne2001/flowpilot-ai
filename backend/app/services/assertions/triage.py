"""ARCH-33 §4.5 — triage: the threshold, the review row, and what a
reviewer's answer teaches.

THIS MODULE HOLDS THE SESSION
=============================

`routing.decide()` makes the decision; this module records it. The split is
what lets the mutation suite kill "threshold compared against the raw score"
offline, and it is the reason the eleven lines that matter are not in here.

A DISCOVERED CONFLICT WITH §4.5, AND HOW IT IS RESOLVED
=======================================================

§4.5 says the triage path "creates the `document_verifications` row". It
cannot always, and the reason is a constraint ARCH-13 already ships:

    uq_document_verifications_open_work_item
        UNIQUE (work_item_id) WHERE status IN ('PENDING', 'DISAGREED')

One open verification per work item. A workflow with three assertion nodes
over one document, or an assertion running on a document whose extraction is
already under review, would violate it on the second insert — and the symptom
would be an IntegrityError inside a worker, after the first assertion had
already routed.

So: ATTACH to the open verification when there is one, CREATE when there is
not. Both paths add exactly one `document_verification_fields` row with
`field_path = assertion:{definition_id}`, which is what §4.5 actually needs —
one reviewable item per assertion — and the reviewer sees one card per
document carrying everything outstanding on it, which is better product than
three cards for one contract.

`agent_count = 2` on a created row is not a claim that two agents ran. The
column carries `CHECK (agent_count >= 2 AND agent_count <= 5)` from ARCH-13's
multi-agent extraction, and 2 is the smallest value the constraint permits.
An assertion has one machine reading and one human reviewer; the number is
recorded here as what it is rather than left to look meaningful.

WHY ASSERTION FIELDS RESOLVE THROUGH THEIR OWN ENDPOINT
=======================================================

`document_verification_service.resolve` requires a chosen VALUE for every
disagreed field and then releases the automation. That is right for an
extracted field and wrong for an assertion: resolving an assertion is not
choosing a value, it is choosing which EDGE a paused execution takes.

So `resolve_assertion()` below owns the assertion half, and `apply_arch33_
final.py` teaches ARCH-13's resolver to skip `assertion:`-prefixed fields
rather than demand values for them. Both halves then close the verification
when nothing is left outstanding, and neither can release an execution the
other was still waiting on.

WHAT A RESOLUTION TEACHES, AND WHAT IT NEVER TOUCHES
====================================================

§4.3: "Nothing rewrites the assertion's rule text; the rule stays what the
administrator wrote." `resolve_assertion` writes:

  * `assertion_evaluations.reviewer_verdict` and `reviewed_at` — the label
    ARCH-35 fits its calibrator on;
  * `assertion_retrieval_phrases` rows with `source = 'REVIEWER'` — the
    phrases from the paragraph the reviewer actually pointed at.

It never touches `assertion_definitions`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.assertion import (
    AssertionDefinition,
    AssertionEvaluation,
    AssertionRetrievalPhrase,
)
from app.models.verification import (
    DisagreementKind,
    DocumentVerification,
    DocumentVerificationField,
    VerificationStatus,
)
from app.services.assertions import calibration, retrieve, routing, vocabulary as vocab

logger = logging.getLogger("app.services.assertions.triage")

__all__ = [
    "TriageOutcome",
    "effective_threshold_for",
    "labels_for",
    "calibration_model_for",
    "record_evaluation",
    "resolve_assertion",
    "learn_phrases_from",
    "meter_evaluation",
    "MIN_LEARNED_PHRASE_CHARS",
    "MAX_LEARNED_PHRASES_PER_RESOLUTION",
]

#: A learned phrase shorter than this matches everything. "the", "days" and
#: "shall" would each be taught by almost every correction and would then
#: dominate the lexical arm of every future query for that family.
MIN_LEARNED_PHRASE_CHARS: int = 8

#: How many phrases one correction may teach. A reviewer pointing at a
#: paragraph is teaching "this is how our contracts word it", not donating the
#: paragraph to the query.
MAX_LEARNED_PHRASES_PER_RESOLUTION: int = 3

#: The ARCH-13 column shape for a verification this phase creates. See the
#: module header: 2 is the floor the CHECK permits, not a count of agents.
ASSERTION_AGENT_COUNT: int = 2


@dataclass(frozen=True)
class TriageOutcome:
    evaluation: AssertionEvaluation
    decision: Any
    verification: Optional[DocumentVerification]
    #: True when this triage created the verification rather than attaching to
    #: one that was already open.
    created_verification: bool = False

    @property
    def edge(self) -> str:
        return self.decision.edge

    @property
    def continues(self) -> bool:
        return self.decision.continues


# ===========================================================================
# Calibration inputs
# ===========================================================================


def labels_for(
    db: Session, *, organization_id: uuid.UUID, family: str, limit: int = 2000
) -> tuple[Any, ...]:
    """Reviewer-resolved evaluations for one (tenant, family), as labels.

    `engine_was_right`, never "the document passed". An engine that correctly
    said FAIL is a CORRECT example; treating it as an error would teach the
    calibrator to distrust every rule that mostly catches violations, which is
    every rule worth writing.
    """
    rows = (
        db.execute(
            select(AssertionEvaluation.raw_score, AssertionEvaluation.verdict,
                   AssertionEvaluation.reviewer_verdict)
            .join(
                AssertionDefinition,
                AssertionDefinition.id == AssertionEvaluation.definition_id,
            )
            .where(
                AssertionEvaluation.organization_id == organization_id,
                AssertionDefinition.family == family,
                AssertionEvaluation.reviewer_verdict.isnot(None),
            )
            .order_by(AssertionEvaluation.created_at.desc())
            .limit(int(limit))
        )
        .all()
    )
    return tuple(
        calibration.LabeledExample(
            raw_score=Decimal(str(raw_score)),
            correct=(reviewer_verdict == verdict),
        )
        for raw_score, verdict, reviewer_verdict in rows
    )


def calibration_model_for(
    db: Session, *, organization_id: uuid.UUID, family: str
) -> Any:
    """Fit the (tenant, family) calibrator from this tenant's own labels.

    Fitted per call rather than cached. Isotonic over a few thousand Decimals
    is microseconds, and a cache here would be a second place where "which
    model produced this probability" is answered — which is exactly the
    question `calibration_model_id` exists to answer. ARCH-35 replaces this
    with a stored, versioned model and inherits the same interface.
    """
    return calibration.fit(
        labels_for(db, organization_id=organization_id, family=family),
        family=family,
    )


def effective_threshold_for(
    db: Session, *, definition: AssertionDefinition
) -> tuple[Decimal, Any]:
    """`(threshold actually applied, calibration model)`.

    The administrator's setting is never lowered and never overwritten. During
    the cold start the floor from §4.6 applies on top of it.
    """
    model = calibration_model_for(
        db,
        organization_id=definition.organization_id,
        family=definition.family,
    )
    return calibration.effective_threshold(definition.threshold, model), model


# ===========================================================================
# Recording an evaluation
# ===========================================================================


def _open_verification(
    db: Session, *, work_item_id: uuid.UUID
) -> Optional[DocumentVerification]:
    from app.models.verification import BLOCKING_STATUSES

    return db.execute(
        select(DocumentVerification)
        .options(selectinload(DocumentVerification.fields))
        .where(
            DocumentVerification.work_item_id == work_item_id,
            DocumentVerification.status.in_(BLOCKING_STATUSES),
        )
        .limit(1)
    ).scalar_one_or_none()


def _disagreement_kind(verdict: str) -> DisagreementKind:
    """UNDETERMINED is MISSING; FAIL and a low-confidence PASS are CONFLICT.

    The distinction is what the reviewer's queue sorts on: MISSING means "we
    could not find it", CONFLICT means "we found it and it does not satisfy
    the rule, or we are not sure enough". Those are different jobs.
    """
    if verdict == vocab.VERDICT_UNDETERMINED:
        return DisagreementKind.MISSING
    return DisagreementKind.CONFLICT


def _attach_review(
    db: Session,
    *,
    definition: AssertionDefinition,
    work_item_id: uuid.UUID,
    evaluation_verdict: str,
    extracted_value: Optional[dict[str, Any]],
    confidence: Decimal,
    evidence: Sequence[dict[str, Any]],
) -> tuple[DocumentVerification, bool]:
    """Create or join the open verification, and add exactly one field row."""
    verification = _open_verification(db, work_item_id=work_item_id)
    created = False

    if verification is None:
        verification = DocumentVerification(
            work_item_id=work_item_id,
            workspace_id=definition.workspace_id,
            organization_id=definition.organization_id,
            status=VerificationStatus.PENDING,
            agent_count=ASSERTION_AGENT_COUNT,
            # PENDING is not terminal, and
            # ck_document_verifications_score_matches_status requires
            # agreement_score to be NULL for exactly that reason.
            agreement_score=None,
            confidence=None,
            cost_micros=0,
            details={"source": "assertion", "assertions": []},
        )
        db.add(verification)
        db.flush()
        created = True

    field_path = vocab.field_path_for(definition.id)
    existing = db.execute(
        select(DocumentVerificationField).where(
            DocumentVerificationField.verification_id == verification.id,
            DocumentVerificationField.field_path == field_path,
        )
    ).scalar_one_or_none()

    if existing is None:
        db.add(
            DocumentVerificationField(
                verification_id=verification.id,
                field_path=field_path,
                agreed=False,
                confidence=confidence,
                consensus_value=extracted_value,
                # The evidence lives on the field so the review screen can
                # highlight without joining back to assertion_evaluations, and
                # so a field that outlives its evaluation still shows a human
                # what was being asked.
                agent_values=list(evidence),
                disagreement_kind=_disagreement_kind(evaluation_verdict),
            )
        )
    else:
        existing.confidence = confidence
        existing.consensus_value = extracted_value
        existing.agent_values = list(evidence)
        existing.disagreement_kind = _disagreement_kind(evaluation_verdict)
        existing.agreed = False
        existing.resolved_value = None

    details = dict(verification.details or {})
    assertions = [
        entry
        for entry in (details.get("assertions") or [])
        if entry.get("definition_id") != str(definition.id)
    ]
    assertions.append(
        {
            "definition_id": str(definition.id),
            "sentence": definition.sentence,
            "family": definition.family,
            "field_path": field_path,
        }
    )
    details["assertions"] = assertions
    details.setdefault("source", "assertion")
    verification.details = details

    db.flush()
    return verification, created


def meter_evaluation(
    db: Session,
    *,
    evaluation: AssertionEvaluation,
    definition: AssertionDefinition,
    input_digest: str,
    token_usage: Any = None,
) -> None:
    """§4.7. One `assertion.evaluation`, plus existing token meters for LLM.

    Keyed on the input digest, not on the evaluation id. A re-run under
    unchanged inputs and an unchanged engine is the same work and must not
    bill twice; a re-run after the rule's threshold moved or the engine was
    fixed is genuinely new work and should.

    The token events are the EXISTING `llm.input_token` / `llm.output_token`
    types. §4.7 is explicit that the LLM family "adds no new cost category",
    and this is where that is either true or not.
    """
    from app.services import usage_service

    usage_service.record_usage(
        db,
        organization_id=definition.organization_id,
        workspace_id=definition.workspace_id,
        event_type=vocab.USAGE_EVENT_ASSERTION_EVALUATION,
        quantity=1,
        resource_type="ASSERTION_EVALUATION",
        resource_id=evaluation.id,
        idempotency_key=(
            f"{vocab.USAGE_EVENT_ASSERTION_EVALUATION}:{input_digest}"
        ),
        details={
            "family": definition.family,
            "evaluation_mode": definition.evaluation_mode,
            "routed_to": evaluation.routed_to,
        },
    )

    if token_usage is None:
        return

    provider = getattr(token_usage, "provider", None)
    for event_type, quantity in (
        ("llm.input_token", int(getattr(token_usage, "prompt_tokens", 0) or 0)),
        ("llm.output_token", int(getattr(token_usage, "completion_tokens", 0) or 0)),
    ):
        if quantity <= 0:
            continue
        usage_service.record_usage(
            db,
            organization_id=definition.organization_id,
            workspace_id=definition.workspace_id,
            event_type=event_type,
            quantity=quantity,
            provider=provider,
            resource_type="ASSERTION_EVALUATION",
            resource_id=evaluation.id,
            idempotency_key=f"{event_type}:assertion:{input_digest}",
            details={"model": getattr(token_usage, "model", None)},
        )


def record_evaluation(
    db: Session,
    *,
    definition: AssertionDefinition,
    evaluation_result: Any,
    node_run_id: uuid.UUID,
    work_item_id: uuid.UUID,
    retrieval: Any = None,
    input_digest: str = "",
    meter: bool = True,
) -> TriageOutcome:
    """Apply the threshold, write the row, and open a review when needed.

    The ORDER here is the whole point and it is the reverse of the obvious
    one. The verification is created BEFORE the evaluation row is inserted,
    because `ck_ae_triage_has_review` refuses a TRIAGE row with a null
    `verification_id` — so a code path that wrote the evaluation first and
    then opened the review would fail at the first flush rather than at the
    second, which is how "the document stopped and nobody was told" gets
    shipped.
    """
    threshold, model = effective_threshold_for(db, definition=definition)
    probability = calibration.calibrate(model, evaluation_result.raw_score)

    decision = routing.decide(
        verdict=evaluation_result.verdict,
        raw_score=evaluation_result.raw_score,
        calibrated_probability=probability,
        effective_threshold=threshold,
    )

    verification: Optional[DocumentVerification] = None
    created = False
    if decision.routed_to == vocab.ROUTE_TRIAGE:
        verification, created = _attach_review(
            db,
            definition=definition,
            work_item_id=work_item_id,
            evaluation_verdict=evaluation_result.verdict,
            extracted_value=evaluation_result.extracted_value,
            confidence=(
                probability
                if probability is not None
                else Decimal(str(evaluation_result.raw_score))
            ),
            evidence=evaluation_result.evidence,
        )

    evaluation = AssertionEvaluation(
        organization_id=definition.organization_id,
        workspace_id=definition.workspace_id,
        definition_id=definition.id,
        node_run_id=node_run_id,
        work_item_id=work_item_id,
        verdict=evaluation_result.verdict,
        extracted_value=evaluation_result.extracted_value,
        raw_score=Decimal(str(evaluation_result.raw_score)),
        calibrated_probability=probability,
        calibration_model_id=(
            uuid.UUID(model.model_id) if getattr(model, "model_id", None) else None
        ),
        routed_to=decision.routed_to,
        verification_id=verification.id if verification is not None else None,
        evidence=list(evaluation_result.evidence),
    )
    db.add(evaluation)
    db.flush()

    if meter:
        meter_evaluation(
            db,
            evaluation=evaluation,
            definition=definition,
            input_digest=input_digest or str(evaluation.id),
            token_usage=getattr(evaluation_result, "token_usage", None),
        )

    # Credit only the phrases that literally occur in the paragraph the
    # verdict rests on. See `retrieve.record_hits`.
    if retrieval is not None and evaluation_result.matched_phrases:
        retrieve.record_hits(
            db,
            organization_id=definition.organization_id,
            family=definition.family,
            phrases=evaluation_result.matched_phrases,
        )

    logger.info(
        "assertion.evaluated",
        extra={
            "definition_id": str(definition.id),
            "work_item_id": str(work_item_id),
            "family": definition.family,
            **decision.as_details(),
        },
    )

    return TriageOutcome(
        evaluation=evaluation,
        decision=decision,
        verification=verification,
        created_verification=created,
    )


# ===========================================================================
# Resolution — what a reviewer's answer teaches
# ===========================================================================


def _phrase_candidates(quote: str) -> tuple[str, ...]:
    """Phrases worth learning from the paragraph a reviewer pointed at.

    Overlapping three- and four-word windows, filtered by length and by
    whether they carry any content at all. Deliberately not the whole
    paragraph: a query seeded with a paragraph matches that paragraph and
    nothing else, which teaches retrieval to find the document it has already
    seen.
    """
    words = [
        word.strip(".,;:()\"'")
        for word in (quote or "").split()
        if word.strip(".,;:()\"'")
    ]
    stop = {
        "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "by",
        "this", "that", "shall", "may", "be", "is", "are", "with", "as",
    }
    candidates: list[str] = []
    for size in (4, 3):
        for index in range(len(words) - size + 1):
            window = words[index : index + size]
            if all(word.lower() in stop for word in window):
                continue
            phrase = " ".join(window).lower()
            if len(phrase) < MIN_LEARNED_PHRASE_CHARS:
                continue
            if len(phrase) > vocab.MAX_PHRASE_LENGTH:
                continue
            if any(char.isdigit() for char in phrase):
                # A phrase containing the contract's own numbers finds THIS
                # contract. "within thirty 30 days" is not how the next
                # supplier words it.
                continue
            candidates.append(phrase)
    return tuple(candidates)


def learn_phrases_from(
    db: Session,
    *,
    organization_id: uuid.UUID,
    family: str,
    quote: str,
    limit: int = MAX_LEARNED_PHRASES_PER_RESOLUTION,
) -> tuple[str, ...]:
    """Write REVIEWER phrases from the paragraph the reviewer chose.

    Idempotent against `uq_arp_org_family_phrase`, which is case-insensitive:
    a phrase taught twice increments nothing and inserts nothing, because the
    phrase's value is that it exists in the table, not how often a reviewer
    happened to pick a paragraph containing it. `hits` is incremented by
    RETRIEVAL, in `retrieve.record_hits`, which is the only place that knows
    whether the phrase actually worked.
    """
    candidates = _phrase_candidates(quote)
    if not candidates:
        return ()

    existing = {
        phrase.casefold()
        for phrase in db.execute(
            select(AssertionRetrievalPhrase.phrase).where(
                AssertionRetrievalPhrase.organization_id == organization_id,
                AssertionRetrievalPhrase.family == family,
            )
        )
        .scalars()
        .all()
    }
    seeds = {phrase.casefold() for phrase in vocab.seed_phrases_for(family)}

    written: list[str] = []
    for phrase in candidates:
        if len(written) >= limit:
            break
        key = phrase.casefold()
        if key in existing or key in seeds:
            continue
        db.add(
            AssertionRetrievalPhrase(
                organization_id=organization_id,
                family=family,
                phrase=phrase,
                source=vocab.PHRASE_SOURCE_REVIEWER,
                hits=0,
            )
        )
        existing.add(key)
        written.append(phrase)

    if written:
        db.flush()
    return tuple(written)


def resolve_assertion(
    db: Session,
    *,
    evaluation: AssertionEvaluation,
    reviewer_verdict: str,
    reviewer_user_id: uuid.UUID,
    corrected_quote: Optional[str] = None,
    corrected_value: Optional[dict[str, Any]] = None,
) -> AssertionEvaluation:
    """Record the reviewer's answer, teach retrieval, close the field.

    Three things happen and a fourth deliberately does not:

      * `reviewer_verdict` / `reviewed_at` are set — the label ARCH-35 fits on.
      * The `document_verification_fields` row is marked resolved.
      * "Wrong paragraph" teaches `assertion_retrieval_phrases`.
      * `assertion_definitions` is not touched. §4.3.
    """
    if reviewer_verdict not in vocab.VERDICTS:
        raise ValueError(
            f"{reviewer_verdict!r} is not a verdict a reviewer can give. "
            f"Known: {', '.join(vocab.VERDICTS)}."
        )
    if evaluation.reviewer_verdict is not None:
        raise ValueError(
            f"Assertion evaluation {evaluation.id} was already resolved as "
            f"{evaluation.reviewer_verdict}. Re-resolving would overwrite the "
            "label ARCH-35 has already learned from."
        )
    if evaluation.routed_to != vocab.ROUTE_TRIAGE:
        raise ValueError(
            f"Assertion evaluation {evaluation.id} continued on the pass "
            "edge and was never sent for review; there is nothing to resolve."
        )

    definition = db.execute(
        select(AssertionDefinition).where(
            AssertionDefinition.id == evaluation.definition_id
        )
    ).scalar_one()

    evaluation.reviewer_verdict = reviewer_verdict
    evaluation.reviewed_at = datetime.now(timezone.utc)

    taught: tuple[str, ...] = ()
    if corrected_quote and corrected_quote.strip():
        taught = learn_phrases_from(
            db,
            organization_id=definition.organization_id,
            family=definition.family,
            quote=corrected_quote,
        )
        evidence = list(evaluation.evidence or [])
        evidence.append(
            {
                "chunk_id": None,
                "quote": corrected_quote,
                "source": "REVIEWER",
                "confidence": 1.0,
                "notes": ["the reviewer selected this paragraph"],
            }
        )
        evaluation.evidence = evidence

    field_path = vocab.field_path_for(definition.id)
    if evaluation.verification_id is not None:
        row = db.execute(
            select(DocumentVerificationField).where(
                DocumentVerificationField.verification_id
                == evaluation.verification_id,
                DocumentVerificationField.field_path == field_path,
            )
        ).scalar_one_or_none()
        if row is not None:
            row.resolved_value = corrected_value or {
                "reviewer_verdict": reviewer_verdict,
                "quote": corrected_quote,
            }

        _close_if_complete(
            db,
            verification_id=evaluation.verification_id,
            reviewer_user_id=reviewer_user_id,
        )

    db.flush()
    logger.info(
        "assertion.resolved",
        extra={
            "evaluation_id": str(evaluation.id),
            "definition_id": str(definition.id),
            "reviewer_verdict": reviewer_verdict,
            "engine_verdict": evaluation.verdict,
            "phrases_taught": len(taught),
        },
    )
    return evaluation


def _close_if_complete(
    db: Session, *, verification_id: uuid.UUID, reviewer_user_id: uuid.UUID
) -> Optional[DocumentVerification]:
    """Mark the verification REVIEWED once nothing on it is outstanding.

    "Outstanding" is any field with `agreed = false` and no `resolved_value`,
    assertion or extraction alike. Closing on the assertion fields alone would
    release an execution while an extracted field a reviewer never looked at
    was still open, which is exactly the partial-resolve failure ARCH-13's own
    resolver refuses.
    """
    verification = db.execute(
        select(DocumentVerification)
        .options(selectinload(DocumentVerification.fields))
        .where(DocumentVerification.id == verification_id)
    ).scalar_one_or_none()
    if verification is None:
        return None

    outstanding = [
        row
        for row in verification.fields
        if not row.agreed and row.resolved_value is None
    ]
    if outstanding:
        return verification

    verification.status = VerificationStatus.REVIEWED
    verification.reviewed_by_user_id = reviewer_user_id
    verification.reviewed_at = datetime.now(timezone.utc)
    verification.auto_approved = False
    # ck_document_verifications_score_matches_status: a terminal status
    # requires a non-null agreement_score. Every field was resolved by a
    # human, so the agreement among them is total by construction.
    if verification.agreement_score is None:
        verification.agreement_score = Decimal("1.0000")
    db.flush()
    return verification
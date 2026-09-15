"""ARCH-33 — request and response shapes for the assertions API.

WHY `plan` IS RESPONSE-ONLY
===========================

There is no `plan` on any request model, deliberately. The compiled plan is
what every parser trusts, and a client that could post one could post any one:
a `presence` plan against a `duration_bound` sentence, a bound of zero, a
family the console never showed the author. The server compiles the sentence
it was given and persists what IT compiled.

That also means the "Understood as:" line the console renders is the same
artefact the engine will use, which is the entire promise of §4.2.

WHY THE THRESHOLD IS A STRING ON THE WIRE
=========================================

`Decimal`, serialised as a string, not a float. `0.95` does not exist in
binary floating point, `threshold numeric(5,4)` does not round, and a slider
that sends 0.9499999999999999 would be refused by
`ck_ad_threshold_bounded` on some values and accepted on others.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AssertionPreviewRequest",
    "AssertionPreviewResponse",
    "AssertionSaveRequest",
    "AssertionDefinitionResponse",
    "AssertionSimulateRequest",
    "AssertionSimulateResponse",
    "AssertionReviewItemResponse",
    "AssertionResolveRequest",
    "AssertionEvaluationResponse",
    "RetrievalPhraseResponse",
]


class AssertionPreviewRequest(BaseModel):
    sentence: str = Field(min_length=1, max_length=400)
    threshold: Optional[Decimal] = None


class AssertionPreviewResponse(BaseModel):
    family: str
    evaluation_mode: str
    #: The compiled plan in plain words. §4.6's "Understood as:" line.
    understood_as: str
    requires_acknowledgement: bool
    reason: Optional[str] = None
    notes: list[str] = Field(default_factory=list)
    plan: dict[str, Any]
    threshold: Decimal
    effective_threshold: Decimal
    #: §4.6's line under the slider, already assembled as prose.
    consequence: str
    enough_labels: bool
    label_count: int
    seed_phrases: list[str] = Field(default_factory=list)


class AssertionSaveRequest(BaseModel):
    node_key: str = Field(min_length=1, max_length=64)
    sentence: str = Field(min_length=1, max_length=400)
    threshold: Decimal
    #: The console's confirmation tick. Required by the server whenever the
    #: sentence falls through to the LLM family, and refused by
    #: `ck_ad_llm_acknowledged` underneath if it somehow is not.
    acknowledge_llm: bool = False


class AssertionDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    node_id: uuid.UUID
    sentence: str
    family: str
    evaluation_mode: str
    threshold: Decimal
    version: int
    plan: dict[str, Any]
    understood_as: str = ""
    llm_acknowledged_by: Optional[uuid.UUID] = None
    created_at: Optional[Any] = None


class AssertionSimulateRequest(BaseModel):
    work_item_id: uuid.UUID
    #: Either a saved definition or a sentence being drafted. The runner in
    #: the rule builder is used BEFORE the step is saved, which is the only
    #: time it can change the author's mind.
    definition_id: Optional[uuid.UUID] = None
    sentence: Optional[str] = Field(default=None, max_length=400)
    threshold: Optional[Decimal] = None


class AssertionSimulateResponse(BaseModel):
    work_item_id: uuid.UUID
    verdict: str
    routed_to: str
    edge: str
    reason: str
    raw_score: Decimal
    calibrated_probability: Optional[Decimal] = None
    effective_threshold: Decimal
    extracted_value: Optional[dict[str, Any]] = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    features: Optional[dict[str, Any]] = None


class AssertionEvaluationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    definition_id: uuid.UUID
    work_item_id: uuid.UUID
    verdict: str
    routed_to: str
    raw_score: Decimal
    calibrated_probability: Optional[Decimal] = None
    reviewer_verdict: Optional[str] = None
    reviewed_at: Optional[Any] = None
    created_at: Optional[Any] = None


class AssertionReviewItemResponse(BaseModel):
    """One card in the review queue. Everything §4.6 asks it to show."""

    evaluation_id: uuid.UUID
    definition_id: uuid.UUID
    work_item_id: uuid.UUID
    verification_id: Optional[uuid.UUID] = None
    #: What the administrator wrote, verbatim. Never the compiled form here —
    #: a reviewer is checking a document against a REQUIREMENT, and
    #: `payment_terms.days <= 30` is not one.
    sentence: str
    understood_as: str
    family: str
    verdict: str
    extracted_value: Optional[dict[str, Any]] = None
    calibrated_probability: Optional[Decimal] = None
    raw_score: Decimal
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    document_name: Optional[str] = None
    created_at: Optional[Any] = None


class AssertionResolveRequest(BaseModel):
    """"It passes", "It fails", or "Wrong paragraph"."""

    #: PASS or FAIL for the first two actions. "Wrong paragraph" sends the
    #: reviewer's verdict plus the span they picked.
    reviewer_verdict: str
    #: Set by "Wrong paragraph". The text of the paragraph the reviewer chose;
    #: retrieval phrases are learned from it and the rule text is untouched.
    corrected_quote: Optional[str] = Field(default=None, max_length=4000)
    corrected_value: Optional[dict[str, Any]] = None


class RetrievalPhraseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    family: str
    phrase: str
    source: str
    hits: int
    created_at: Optional[Any] = None
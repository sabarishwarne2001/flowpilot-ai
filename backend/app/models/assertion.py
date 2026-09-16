"""ARCH-33 §4.4 — assertion definitions, evaluations and learned phrases.

THE CONSTRAINT THAT CARRIES THE PHASE
=====================================

`ck_ae_routing_consistent`:

    CHECK (routed_to = 'TRIAGE'
           OR (verdict = 'PASS' AND calibrated_probability IS NOT NULL))

Nothing reaches the `pass` edge without a PASS verdict and a calibrated
probability. A code path that forgets to calibrate cannot write a passing row.

This is in the database and not only in the service for the same reason
ARCH-32 put `ck_rj_completed_is_sealed` there. Every service-level expression
of the rule survives exactly as long as nobody reorders the statements around
it: a `routed_to` set before the commit that writes the probability, an
exception swallowed by a worker's retry wrapper, a process killed between two
flushes. None of those can produce a passing row here.

The commercial claim of this phase is "documents that clearly pass move on
automatically". The row asserting one did is the only record of that decision,
so it is the row that must be impossible to write dishonestly.

`verify_arch33.py --db` drives an INSERT setting `routed_to = 'PASS'` with a
null `calibrated_probability` inside a rolled-back transaction and requires
Postgres to refuse it.

THE SECOND HALF, AND WHY IT IS A SEPARATE CONSTRAINT
====================================================

`ck_ae_triage_has_review`: a row routed to TRIAGE must carry a
`verification_id`. Together with the one above, the two exhaust the space —
every row either continued with a calibrated probability, or stopped with a
review attached. A row that did neither is a document that fell out of the
workflow silently, which is the failure an automation customer notices last
and forgives least.

They are two constraints rather than one compound expression because the error
message matters. "Routed to PASS without a calibrated probability" and "routed
to TRIAGE with nothing for a reviewer to open" are different bugs in different
modules, and a single CHECK would report both as the same violation.

WHY BOTH `organization_id` AND `workspace_id` ON EVERY TABLE
============================================================

ARCH-02. A child table scoped only through its parent is a child table
somebody eventually queries without the join. `assertion_retrieval_phrases` is
the one that would be most tempting to scope loosely — it holds phrases, not
document content — and it is exactly the table where that would leak most
usefully: the phrases a tenant's reviewers taught the system are a map of how
that tenant's contracts are worded.

`assertion_retrieval_phrases` carries `organization_id` only, deliberately and
per §4.4's DDL: §4.3 scopes the self-healing synonym table to the TENANT, not
the workspace. A phrase learned in the procurement workspace helps the legal
workspace read the same supplier's paper, and splitting the table by workspace
would make every workspace relearn the same vocabulary.

WHY `plan` IS JSONB AND NOT COLUMNS
===================================

A compiled plan has a different shape per family: `bound` and `unit` for the
numeric families, `values` for `enumerated`, neither for `presence`. Eight
families across flat columns is eight mostly-null columns and a CHECK
constraint per family to keep them consistent.

The jsonb costs one thing — the database cannot validate the plan's interior —
and `compiler.py` is the only writer, `AssertionPlan.as_json()` is the only
serialiser, and `ck_ad_plan_object` at least guarantees it is an object rather
than a string somebody double-encoded.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin

# ---------------------------------------------------------------------------
# Vocabulary — read, not declared. Same arrangement as
# `app/models/redaction.py`: the closed enums live in a stdlib-only module so
# the pure engines can import them without dragging in the declarative
# registry, and this module reads them so the CHECK constraints and the
# parsers cannot disagree about what a family is.
# ---------------------------------------------------------------------------

from app.services.assertions import vocabulary as vocab  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover
    from app.models.automation_graph import AutomationNode
    from app.models.verification import DocumentVerification


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class AssertionDefinition(Base, UUIDMixin):
    """One compiled assertion, versioned, attached to one automation node."""

    __tablename__ = "assertion_definitions"

    __table_args__ = (
        CheckConstraint(
            f"family IN ({_quoted(vocab.FAMILIES)})",
            name="ck_ad_family_known",
        ),
        # The biconditional, not an implication. `family = 'llm'` and
        # `evaluation_mode = 'LLM'` must agree in BOTH directions: a typed
        # family billing as assistant usage is a cost surprise, and an `llm`
        # family claiming to be deterministic is an unlabelled AI check —
        # which is precisely what §4.2 forbids.
        CheckConstraint(
            f"(family = '{vocab.FAMILY_LLM}') "
            f"= (evaluation_mode = '{vocab.MODE_LLM}')",
            name="ck_ad_mode_matches_family",
        ),
        # §4.2: an untypeable sentence is saved "only as an explicitly
        # LLM-evaluated assertion, labeled as such". The label is a tick in
        # the console; this is what makes the tick load-bearing rather than
        # decorative. A console that forgot to render it cannot save a row.
        CheckConstraint(
            f"evaluation_mode <> '{vocab.MODE_LLM}' "
            "OR llm_acknowledged_by IS NOT NULL",
            name="ck_ad_llm_acknowledged",
        ),
        CheckConstraint(
            f"threshold > {vocab.THRESHOLD_MIN_EXCLUSIVE} "
            f"AND threshold < {vocab.THRESHOLD_MAX_EXCLUSIVE}",
            name="ck_ad_threshold_bounded",
        ),
        CheckConstraint(
            "jsonb_typeof(plan) = 'object'", name="ck_ad_plan_object"
        ),
        CheckConstraint("version >= 1", name="ck_ad_version_positive"),
        CheckConstraint("btrim(sentence) <> ''", name="ck_ad_sentence_present"),
        UniqueConstraint("node_id", "version", name="uq_ad_node_version"),
        Index("ix_ad_workspace_node", "workspace_id", "node_id"),
        Index("ix_ad_organization_family", "organization_id", "family"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Exactly what the administrator typed. Never rewritten — §4.3's
    #: self-healing paragraph is explicit that "nothing rewrites the
    #: assertion's rule text; the rule stays what the administrator wrote".
    sentence: Mapped[str] = mapped_column(Text, nullable=False)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    threshold: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    evaluation_mode: Mapped[str] = mapped_column(String(16), nullable=False)

    #: Who ticked the confirmation. SET NULL on user delete rather than
    #: RESTRICT: the acknowledgement is a historical fact about the rule, and
    #: it must not stop an offboarding. The CHECK above is satisfied at INSERT
    #: time, which is when the decision was actually made.
    llm_acknowledged_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    #: Edits create a new version rather than mutating this row. An evaluation
    #: points at the definition it actually ran, so a rule tightened on Friday
    #: does not retroactively relabel Thursday's passes.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    node: Mapped["AutomationNode"] = relationship("AutomationNode")
    evaluations: Mapped[list["AssertionEvaluation"]] = relationship(
        "AssertionEvaluation",
        back_populates="definition",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return (
            f"<AssertionDefinition {self.id} family={self.family} "
            f"v{self.version}>"
        )


class AssertionEvaluation(Base, UUIDMixin):
    """One run of one assertion against one work item."""

    __tablename__ = "assertion_evaluations"

    __table_args__ = (
        CheckConstraint(
            f"verdict IN ({_quoted(vocab.VERDICTS)})",
            name="ck_ae_verdict_known",
        ),
        CheckConstraint(
            f"routed_to IN ({_quoted(vocab.ROUTES)})",
            name="ck_ae_route_known",
        ),
        # The safety invariant. See the module header.
        CheckConstraint(
            f"routed_to = '{vocab.ROUTE_TRIAGE}' "
            f"OR (verdict = '{vocab.VERDICT_PASS}' "
            "AND calibrated_probability IS NOT NULL)",
            name="ck_ae_routing_consistent",
        ),
        CheckConstraint(
            f"routed_to = '{vocab.ROUTE_PASS}' OR verification_id IS NOT NULL",
            name="ck_ae_triage_has_review",
        ),
        CheckConstraint(
            "raw_score >= 0 AND raw_score <= 1",
            name="ck_ae_raw_score_unit_interval",
        ),
        CheckConstraint(
            "calibrated_probability IS NULL OR "
            "(calibrated_probability >= 0 AND calibrated_probability <= 1)",
            name="ck_ae_probability_unit_interval",
        ),
        # A reviewer verdict and a review timestamp travel together. One
        # without the other is a resolution nobody can date or a date with no
        # resolution, and the resumption logic reads both.
        CheckConstraint(
            "(reviewer_verdict IS NULL) = (reviewed_at IS NULL)",
            name="ck_ae_review_fields_together",
        ),
        CheckConstraint(
            f"reviewer_verdict IS NULL OR reviewer_verdict IN "
            f"({_quoted(vocab.VERDICTS)})",
            name="ck_ae_reviewer_verdict_known",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence) = 'array'", name="ck_ae_evidence_is_array"
        ),
        Index(
            "ix_ae_definition_created",
            "definition_id",
            text("created_at DESC"),
        ),
        Index("ix_ae_workspace_routed", "workspace_id", "routed_to"),
        Index(
            "ix_ae_pending_review",
            "verification_id",
            postgresql_where=text("reviewer_verdict IS NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: RESTRICT, not CASCADE. An evaluation is the evidence that a decision
    #: was made about a document; deleting the rule must not delete the record
    #: of what it decided. Superseding a rule writes a new version.
    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assertion_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    node_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation_node_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )

    verdict: Mapped[str] = mapped_column(String(12), nullable=False)
    extracted_value: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    raw_score: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    calibrated_probability: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(6, 5), nullable=True
    )

    #: ARCH-35's model version, when there is one.
    #:
    #: ARCH35-S1:calibration-model-fk. ARCH-33 created this column bare and
    #: nullable so ARCH-35 could attach the constraint in its own migration
    #: (`arch35_step1_calibration`) without touching ARCH-33's table. SET NULL:
    #: an evaluation is evidence of a decision and outlives the model version
    #: that informed it.
    calibration_model_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "calibration_models.id",
            ondelete="SET NULL",
            name="fk_ae_calibration_model",
        ),
        nullable=True,
    )

    routed_to: Mapped[str] = mapped_column(String(8), nullable=False)

    verification_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_verifications.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: The paragraphs relied on, each with its quote, span and confidence.
    #: An array, never an object: `ck_ae_evidence_is_array` enforces it,
    #: because the console iterates it and an object would render as one
    #: nameless row.
    evidence: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    #: Set only for LLM mode. The token usage this evaluation caused, through
    #: the tenant's existing model route. No FK for the same reason the
    #: calibration model has none plus one more: usage events are partitioned
    #: and archived on their own schedule, and a FK would pin them.
    usage_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    reviewer_verdict: Mapped[Optional[str]] = mapped_column(
        String(12), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    definition: Mapped[AssertionDefinition] = relationship(
        "AssertionDefinition", back_populates="evaluations"
    )
    verification: Mapped[Optional["DocumentVerification"]] = relationship(
        "DocumentVerification"
    )

    @property
    def continues(self) -> bool:
        """Whether execution took the `pass` edge."""
        return self.routed_to == vocab.ROUTE_PASS

    @property
    def awaiting_review(self) -> bool:
        return self.routed_to == vocab.ROUTE_TRIAGE and self.reviewer_verdict is None

    @property
    def engine_was_right(self) -> Optional[bool]:
        """The label `calibration.LabeledExample` is built from.

        None until a reviewer has resolved it. Compares the reviewer's verdict
        to the ENGINE's verdict, not to PASS: see `LabeledExample`'s docstring
        for why the distinction decides what the calibrator learns.
        """
        if self.reviewer_verdict is None:
            return None
        return self.reviewer_verdict == self.verdict

    def __repr__(self) -> str:
        return (
            f"<AssertionEvaluation {self.id} verdict={self.verdict} "
            f"routed_to={self.routed_to} p={self.calibrated_probability}>"
        )


class AssertionRetrievalPhrase(Base, UUIDMixin):
    """A phrase that located the right paragraph, scoped to the tenant.

    §4.3's self-healing, half one: "The synonym table for that family gains
    retrieval phrases that located the right paragraph, scoped to the tenant."

    Rows arrive two ways. `SEED` rows are materialised from
    `vocabulary.SEED_PHRASES` so the console can show an administrator what
    retrieval is searching for. `REVIEWER` rows come from a reviewer using
    "Wrong paragraph" and picking the right one — the phrases in the paragraph
    they picked, which the seeds missed.

    `hits` is what stops the table growing into noise. A reviewer-taught
    phrase that never locates another paragraph stays at zero and can be
    pruned; one that keeps working earns its place.
    """

    __tablename__ = "assertion_retrieval_phrases"

    __table_args__ = (
        CheckConstraint(
            f"family IN ({_quoted(vocab.FAMILIES)})",
            name="ck_arp_family_known",
        ),
        CheckConstraint(
            f"source IN ({_quoted(vocab.PHRASE_SOURCES)})",
            name="ck_arp_source_known",
        ),
        CheckConstraint("hits >= 0", name="ck_arp_hits_non_negative"),
        CheckConstraint("btrim(phrase) <> ''", name="ck_arp_phrase_present"),
        # Case-insensitive uniqueness. "Net 30" and "net 30" are one phrase,
        # and two rows for them would double that phrase's weight in every
        # query built from this table.
        Index(
            "uq_arp_org_family_phrase",
            "organization_id",
            "family",
            text("lower(phrase)"),
            unique=True,
        ),
        Index(
            "ix_arp_org_family_hits",
            "organization_id",
            "family",
            text("hits DESC"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    phrase: Mapped[str] = mapped_column(
        String(vocab.MAX_PHRASE_LENGTH), nullable=False
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    hits: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<AssertionRetrievalPhrase {self.family}:{self.phrase!r} "
            f"source={self.source} hits={self.hits}>"
        )


__all__ = [
    "AssertionDefinition",
    "AssertionEvaluation",
    "AssertionRetrievalPhrase",
]
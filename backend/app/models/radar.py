"""ARCH-34 §5.4 — fingerprints, findings and suppressions.

THE CONSTRAINTS THAT CARRY THE PHASE
====================================

`ck_af_pairwise_has_counterpart` and `ck_af_evidence_present`:

    CHECK (layer IN ('PRICE_SURGE') OR counterpart_work_item_id IS NOT NULL)
    CHECK (jsonb_typeof(evidence) = 'array' AND jsonb_array_length(evidence) > 0)

A duplicate finding that cannot name the document it duplicates, or that
carries no evidence, is an accusation. `findings.py` mirrors both rules so the
refusal arrives with an explanation, and the database is the authority because
the service's version survives only as long as nobody reorders the statements
around it.

VOCABULARY IS READ, NOT DECLARED
================================

Same arrangement as `app/models/assertion.py` and `app/models/redaction.py`:
the closed enums live in `app/services/radar/vocabulary.py`, a stdlib-only
module the pure engines can import without dragging in the declarative
registry. This module reads them, so the CHECK constraints and the detectors
cannot disagree about what a layer is.

WHY `pair_lo` / `pair_hi` ARE MAPPED BUT NEVER ASSIGNED
=======================================================

They are `GENERATED ALWAYS ... STORED` columns computing `LEAST` and
`GREATEST` over the pair, and the unique index that makes `(A, B)` and
`(B, A)` collide is built on them. They are mapped read-only so that a query
can order by them and so that `verify_arch34.py` can read them back; assigning
one raises in Postgres, which is the correct outcome — the canonical ordering
is the database's job precisely so no writer can get it wrong.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CHAR,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin
from app.services.radar import vocabulary as vocab

if TYPE_CHECKING:  # pragma: no cover
    from app.models.user import User
    from app.models.work_item import WorkItem


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class DocumentFingerprint(Base):
    """One row per work item: the four comparable things, plus the refusals.

    The primary key is `work_item_id` and not a surrogate. A work item cannot
    have two fingerprints — re-running the fingerprint job after a re-OCR must
    UPDATE, because two rows for one document would each be compared against
    every candidate and the radar would report the document as a duplicate of
    itself under a different id.
    """

    __tablename__ = "document_fingerprints"

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        primary_key=True,
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

    #: L0's evidence. Copied from `uploaded_files.checksum_sha256`, never
    #: recomputed: re-hashing means re-reading the object out of storage, and
    #: ARCH-06 already did it once at upload.
    content_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)

    #: L1's evidence. Nullable — plenty of documents yield neither, and L1
    #: declines rather than matching two of them on emptiness.
    vendor_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    document_number: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )

    #: L2. `bigint[]`, not `integer[]`: a MinHash value is 32-bit UNSIGNED and
    #: Postgres `integer` is signed, so roughly half of every signature would
    #: overflow at INSERT time inside the fingerprint job.
    minhash: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False
    )
    #: Zero means the document produced no shingles. Load-bearing: two empty
    #: signatures are identical, so without this L2 cannot tell "no text" from
    #: "no overlap" and every failed OCR would pair with every other.
    shingle_count: Mapped[int] = mapped_column(Integer, nullable=False)

    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    line_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    #: L2's corroborating guards. `l2_minhash.py` treats absence as "not
    #: measured" rather than "different", which is what keeps recall from
    #: becoming a function of extraction quality.
    document_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    total_micros: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)

    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    #: L3. Added by raw DDL in the migration because SQLAlchemy core has no
    #: pgvector type; mapped here through `pgvector.sqlalchemy`, exactly as
    #: `document_chunks.embedding` is.
    embedding: Mapped[Any] = mapped_column(
        Vector(vocab.EMBEDDING_DIMENSION), nullable=False
    )

    work_item: Mapped["WorkItem"] = relationship("WorkItem")

    __table_args__ = (
        CheckConstraint(
            f"array_length(minhash, 1) = {vocab.MINHASH_PERMUTATIONS}",
            name="ck_df_minhash_length",
        ),
        CheckConstraint(
            "shingle_count >= 0", name="ck_df_shingle_count_non_negative"
        ),
        CheckConstraint("line_count >= 0", name="ck_df_line_count_non_negative"),
        CheckConstraint(
            "page_count IS NULL OR page_count > 0",
            name="ck_df_page_count_positive",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_df_sha256_lowercase_hex"
        ),
        CheckConstraint(
            "vendor_key IS NULL OR btrim(vendor_key) <> ''",
            name="ck_df_vendor_key_present_or_null",
        ),
        CheckConstraint(
            "document_number IS NULL OR btrim(document_number) <> ''",
            name="ck_df_document_number_present_or_null",
        ),
        CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="ck_df_currency_iso"
        ),
        Index("ix_df_workspace_sha", "workspace_id", "content_sha256"),
        Index(
            "ix_df_workspace_vendor_number",
            "workspace_id",
            "vendor_key",
            "document_number",
            postgresql_where=text(
                "vendor_key IS NOT NULL AND document_number IS NOT NULL"
            ),
        ),
        Index(
            "ix_df_workspace_vendor_date",
            "workspace_id",
            "vendor_key",
            "document_date",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DocumentFingerprint work_item={self.work_item_id} "
            f"shingles={self.shingle_count}>"
        )


class AnomalyFinding(Base, UUIDMixin):
    """One thing the radar noticed, with what it noticed it from."""

    __tablename__ = "anomaly_findings"

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

    #: kind AND layer. kind is what the console groups by and what ARCH-13
    #: filters on; layer is what tells the reader how much to trust it. Layer
    #: cannot be derived from kind — DUPLICATE_DOCUMENT covers four very
    #: different strengths of claim.
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    layer: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)

    subject_work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    counterpart_work_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=True,
    )

    score: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    #: Written from the metrics by a template, never generated. §5.6: the
    #: sentence on screen is exactly what the numbers say.
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'OPEN'")
    )
    resolved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: Finding IDENTITY — which series a PRICE_SURGE belongs to.
    dedupe_key: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    #: RECOMPUTE NECESSITY — engine version, thresholds, compared inputs. A row
    #: can keep its dedupe_key while its input_digest changes; merging the two
    #: would make a version bump create duplicate findings.
    input_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    #: READ-ONLY. Postgres computes these; assigning one raises, which is the
    #: point — the canonical ordering cannot be got wrong by a writer that
    #: does not compute it.
    pair_lo: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        Computed(
            "LEAST(subject_work_item_id, counterpart_work_item_id)", persisted=True
        ),
        nullable=True,
    )
    pair_hi: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        Computed(
            "GREATEST(subject_work_item_id, counterpart_work_item_id)",
            persisted=True,
        ),
        nullable=True,
    )

    subject: Mapped["WorkItem"] = relationship(
        "WorkItem", foreign_keys=[subject_work_item_id]
    )
    counterpart: Mapped[Optional["WorkItem"]] = relationship(
        "WorkItem", foreign_keys=[counterpart_work_item_id]
    )
    resolved_by: Mapped[Optional["User"]] = relationship("User")

    __table_args__ = (
        CheckConstraint(f"kind IN ({_quoted(vocab.KINDS)})", name="ck_af_kind_known"),
        CheckConstraint(
            f"layer IN ({_quoted(vocab.LAYERS)})", name="ck_af_layer_known"
        ),
        CheckConstraint(
            f"severity IN ({_quoted(vocab.SEVERITIES)})", name="ck_af_severity_known"
        ),
        CheckConstraint(
            f"status IN ({_quoted(vocab.STATUSES)})", name="ck_af_status_known"
        ),
        CheckConstraint(
            f"(layer IN ({_quoted(vocab.DUPLICATE_LAYER_ORDER)}) AND kind = "
            "'DUPLICATE_DOCUMENT') OR (layer = 'PRICE_SURGE' AND kind = "
            "'PRICE_SURGE') OR (layer = 'CONTRACT_DRIFT' AND kind = "
            "'CONTRACT_DRIFT')",
            name="ck_af_kind_matches_layer",
        ),
        CheckConstraint(
            f"layer IN ({_quoted(tuple(sorted(vocab.UNPAIRED_LAYERS)))}) "
            "OR counterpart_work_item_id IS NOT NULL",
            name="ck_af_pairwise_has_counterpart",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence) = 'array' AND jsonb_array_length(evidence) > 0",
            name="ck_af_evidence_present",
        ),
        CheckConstraint(
            "counterpart_work_item_id IS NULL "
            "OR counterpart_work_item_id <> subject_work_item_id",
            name="ck_af_counterpart_is_not_subject",
        ),
        CheckConstraint("score >= 0 AND score <= 1", name="ck_af_score_unit_interval"),
        CheckConstraint("btrim(headline) <> ''", name="ck_af_headline_present"),
        CheckConstraint(
            "status = 'OPEN' "
            "OR (resolved_by_user_id IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_af_resolution_has_actor",
        ),
        CheckConstraint(
            "status <> 'DISMISSED' OR btrim(coalesce(resolution_note, '')) <> ''",
            name="ck_af_dismissal_has_reason",
        ),
        CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$'", name="ck_af_dedupe_key_hex"
        ),
        CheckConstraint(
            "input_digest ~ '^[0-9a-f]{64}$'", name="ck_af_input_digest_hex"
        ),
        Index(
            "uq_af_pair_layer",
            "workspace_id",
            "layer",
            "pair_lo",
            "pair_hi",
            unique=True,
            postgresql_where=text("counterpart_work_item_id IS NOT NULL"),
        ),
        Index(
            "uq_af_dedupe_unpaired",
            "workspace_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("counterpart_work_item_id IS NULL"),
        ),
        Index(
            "ix_af_feed",
            "workspace_id",
            "status",
            "severity",
            text("created_at DESC"),
        ),
        Index(
            "ix_af_kind_feed",
            "workspace_id",
            "kind",
            "status",
            text("created_at DESC"),
        ),
        Index("ix_af_subject", "subject_work_item_id", "status"),
        Index(
            "ix_af_counterpart",
            "counterpart_work_item_id",
            "status",
            postgresql_where=text("counterpart_work_item_id IS NOT NULL"),
        ),
        Index("ix_af_input_digest", "workspace_id", "input_digest"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AnomalyFinding {self.kind}/{self.layer} "
            f"{self.severity} {self.status}>"
        )


class AnomalySuppression(Base, UUIDMixin):
    """A reviewer's "this is fine, and here is why".

    The finding it came from is never deleted. A dismissed finding is a record
    that the engine raised something and a named person judged it; deleting it
    erases both halves, and the audit question after a double payment is
    exactly "did the system see this, and what did we do".
    """

    __tablename__ = "anomaly_suppressions"

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
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    #: The layer the suppression was granted AT. Suppressing a weak L3 guess
    #: must not also suppress a future byte-identical L0 match on the same
    #: pair: "these two similar quotes are not duplicates" is not "never tell
    #: me if somebody uploads the exact same file twice".
    layer: Mapped[str] = mapped_column(String(16), nullable=False)

    item_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_b_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=True,
    )
    vendor_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sku: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: NULL means never expires, and is deliberately expressible — some
    #: suppressions genuinely are structural. A default of permanent would be
    #: a permanent blind spot granted by somebody at 17:40 on a Friday.
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by: Mapped["User"] = relationship("User")

    __table_args__ = (
        CheckConstraint(f"kind IN ({_quoted(vocab.KINDS)})", name="ck_as_kind_known"),
        CheckConstraint(
            f"layer IN ({_quoted(vocab.LAYERS)})", name="ck_as_layer_known"
        ),
        CheckConstraint("btrim(reason) <> ''", name="ck_as_reason_present"),
        CheckConstraint(
            "item_b_id IS NULL OR item_b_id <> item_a_id", name="ck_as_items_distinct"
        ),
        CheckConstraint(
            f"(layer IN ({_quoted(tuple(sorted(vocab.UNPAIRED_LAYERS)))}) "
            "AND item_b_id IS NULL AND vendor_key IS NOT NULL) "
            f"OR (layer NOT IN ({_quoted(tuple(sorted(vocab.UNPAIRED_LAYERS)))}) "
            "AND item_b_id IS NOT NULL)",
            name="ck_as_scope_matches_layer",
        ),
        Index(
            "uq_as_series",
            "workspace_id",
            "layer",
            "vendor_key",
            "sku",
            unique=True,
            postgresql_where=text("item_b_id IS NULL"),
        ),
        Index("ix_as_workspace_kind", "workspace_id", "kind"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AnomalySuppression {self.kind}/{self.layer}>"


__all__ = ["DocumentFingerprint", "AnomalyFinding", "AnomalySuppression"]

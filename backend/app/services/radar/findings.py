"""ARCH-34 §5.5 — writing a finding, once, with its evidence attached.

HEADLINES ARE TEMPLATED FROM THE METRICS, NEVER GENERATED
=========================================================

§5.6 is explicit and it is the right call. Every sentence this module produces
is a format string over numbers the detectors computed, so "98% of line items
match" is the Jaccard estimate rendered, not a summary of it. There is no LLM
on this path, no paraphrase, and no adjective that is not a direct function of
a number.

The alternative is a sentence that reads better and occasionally is not true,
shown to somebody deciding whether to hold a payment.

UPSERT, NOT INSERT
==================

A pairwise finding's identity is `(workspace, layer, pair_lo, pair_hi)`, which
Postgres computes. An unpaired finding's identity is
`(workspace, dedupe_key)`. Either way, re-running a detector over a pair that
already has an open finding must UPDATE it — not insert a second one, and not
silently skip, because the score and the evidence may have moved.

What it must NOT do is reopen a finding a person already closed. `upsert()`
leaves a CONFIRMED or DISMISSED row's status alone and refreshes only the
metrics. A reviewer who dismissed a duplicate and watched it come back the
next night would, correctly, stop trusting the queue.

IDEMPOTENCE IS MEASURED ON `input_digest`
=========================================

`upsert()` returns `changed=False` when the digest it was handed equals the
digest already on the row. `sweep.py` uses that to decide whether to emit a
usage event, which is what makes an idle nightly run cost nothing — §5.7's
reasoning about incentives, enforced rather than asserted.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.radar import AnomalyFinding
from app.services import audit_service, outbox_service
from app.services.radar import vocabulary as vocab

logger = logging.getLogger("app.services.radar.findings")

__all__ = [
    "FindingDraft",
    "UpsertResult",
    "dedupe_key_for",
    "headline_for",
    "upsert",
    "emit_detected",
]


@dataclass(frozen=True)
class FindingDraft:
    """Everything needed to write one finding, already decided."""

    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    layer: str
    severity: str
    subject_work_item_id: uuid.UUID
    counterpart_work_item_id: Optional[uuid.UUID]
    score: Decimal
    headline: str
    metrics: dict[str, Any]
    evidence: Sequence[dict[str, Any]]
    input_digest: str
    dedupe_key: str

    @property
    def kind(self) -> str:
        return vocab.kind_for_layer(self.layer)

    def validate(self) -> None:
        """Mirror the two CHECK constraints, so the refusal explains itself.

        The database is still the authority. This exists so that a bug in a
        detector fails at the call site with a sentence about evidence rather
        than as an IntegrityError six frames up with a constraint name.
        """
        if (
            self.layer in vocab.PAIRWISE_LAYERS
            and self.counterpart_work_item_id is None
        ):
            raise ValueError(
                f"A {self.layer} finding must name the document it is about. "
                "ck_af_pairwise_has_counterpart refuses this row, and it "
                "should: a duplicate finding with no counterpart is an "
                "accusation with nothing behind it."
            )
        if not self.evidence:
            raise ValueError(
                f"A {self.layer} finding was drafted with no evidence. "
                "ck_af_evidence_present refuses this row. A reviewer who "
                "cannot check a finding has to open both documents and read "
                "them, which is the work the radar exists to do."
            )


@dataclass(frozen=True)
class UpsertResult:
    finding: AnomalyFinding
    created: bool
    #: False when the row already carried this exact `input_digest`. The
    #: sweep's billing decision hangs on it.
    changed: bool


# ===========================================================================
# Identity and headlines
# ===========================================================================


def dedupe_key_for(
    *,
    layer: str,
    subject_work_item_id: Any,
    counterpart_work_item_id: Any = None,
    vendor_key: Optional[str] = None,
    sku: Optional[str] = None,
) -> str:
    """SHA-256 over the identity of the thing being claimed.

    For a pair, over the SORTED ids, so `(A, B)` and `(B, A)` hash the same.
    That duplicates what the generated columns already enforce, deliberately:
    the constraint is the authority and this is what lets a caller look a
    finding up before trying to write one.

    For a surge, over `(subject, vendor_key, sku)` — the series — because a
    surge has no pair and a unique index containing a NULL does not collide
    with itself. Without this, every nightly run would insert the same surge
    again, forever.
    """
    if layer in vocab.PAIRWISE_LAYERS:
        if counterpart_work_item_id is None:
            raise ValueError(
                f"{layer} is a pairwise layer; its dedupe key needs both ids."
            )
        pair = sorted([str(subject_work_item_id), str(counterpart_work_item_id)])
        material = "|".join([layer, *pair])
    else:
        if not vendor_key:
            raise ValueError(
                f"{layer} identifies a series, so its dedupe key needs a "
                "vendor key. Without one, two different suppliers' surges on "
                "the same document would collide onto one row."
            )
        material = "|".join(
            [layer, str(subject_work_item_id), vendor_key, sku or ""]
        )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _percent(value: Any) -> str:
    return f"{Decimal(str(value)) * 100:.0f}%"


def headline_for(
    *, layer: str, metrics: dict[str, Any], subject_label: str, counterpart_label: str
) -> str:
    """The sentence on screen, as a direct function of the numbers.

    Labels are the documents' own filenames or extracted numbers, supplied by
    the caller — this module does not read the database.
    """
    if layer == vocab.LAYER_L0:
        return (
            f"{subject_label} is byte-for-byte identical to {counterpart_label}."
        )
    if layer == vocab.LAYER_L1:
        return (
            f"{subject_label} carries the same document number as "
            f"{counterpart_label} from the same supplier "
            f"({metrics.get('document_number', 'unknown')})."
        )
    if layer == vocab.LAYER_L2:
        share = _percent(metrics.get("jaccard_estimate", "0"))
        gap = metrics.get("date_gap_days")
        when = f", dated {gap} days apart" if gap is not None else ""
        return (
            f"{share} of line items on {subject_label} match "
            f"{counterpart_label}{when}."
        )
    if layer == vocab.LAYER_L3:
        share = _percent(metrics.get("cosine", "0"))
        return (
            f"{subject_label} reads {share} similar to {counterpart_label}. "
            "Similarity is evidence, not proof."
        )
    if layer == vocab.LAYER_PRICE_SURGE:
        change = Decimal(str(metrics.get("relative_change", "0"))) * 100
        direction = "rose" if change >= 0 else "fell"
        count = metrics.get("observation_count", 0)
        if metrics.get("basis") == vocab.BASIS_RELATIVE_ONLY:
            return (
                f"{metrics.get('sku', 'This item')} {direction} "
                f"{abs(change):.1f}% against a price that had not moved in "
                f"{count} orders."
            )
        return (
            f"{metrics.get('sku', 'This item')} {direction} {abs(change):.1f}% "
            f"above its median over {count} orders."
        )
    # CONTRACT_DRIFT
    label = metrics.get("family_label", "A clause")
    left = metrics.get("subject_value", "?")
    right = metrics.get("counterpart_value", "?")
    return (
        f"{label} differs between {subject_label} ({left}) and "
        f"{counterpart_label} ({right})."
    )


# ===========================================================================
# Persistence
# ===========================================================================


def _existing(db: Session, draft: FindingDraft) -> Optional[AnomalyFinding]:
    """Find the row this draft is an update of, by whichever identity applies."""
    if draft.counterpart_work_item_id is not None:
        lo, hi = sorted(
            [str(draft.subject_work_item_id), str(draft.counterpart_work_item_id)]
        )
        stmt = select(AnomalyFinding).where(
            AnomalyFinding.workspace_id == draft.workspace_id,
            AnomalyFinding.layer == draft.layer,
            AnomalyFinding.pair_lo == uuid.UUID(lo),
            AnomalyFinding.pair_hi == uuid.UUID(hi),
        )
    else:
        stmt = select(AnomalyFinding).where(
            AnomalyFinding.workspace_id == draft.workspace_id,
            AnomalyFinding.dedupe_key == draft.dedupe_key,
            AnomalyFinding.counterpart_work_item_id.is_(None),
        )
    return db.execute(stmt).scalar_one_or_none()


def upsert(
    db: Session, draft: FindingDraft, *, actor_id: Optional[uuid.UUID] = None
) -> UpsertResult:
    """Write the finding once. Returns whether anything actually changed."""
    draft.validate()

    existing = _existing(db, draft)

    if existing is None:
        finding = AnomalyFinding(
            organization_id=draft.organization_id,
            workspace_id=draft.workspace_id,
            kind=draft.kind,
            layer=draft.layer,
            severity=draft.severity,
            subject_work_item_id=draft.subject_work_item_id,
            counterpart_work_item_id=draft.counterpart_work_item_id,
            score=draft.score,
            headline=draft.headline,
            metrics=dict(draft.metrics),
            evidence=[dict(item) for item in draft.evidence],
            status=vocab.STATUS_OPEN,
            dedupe_key=draft.dedupe_key,
            input_digest=draft.input_digest,
            engine_version=vocab.ENGINE_VERSION,
        )
        db.add(finding)
        db.flush()
        audit_service.record(
            db,
            organization_id=draft.organization_id,
            actor_id=actor_id,
            resource_type=AuditResourceType.WORK_ITEM,
            resource_id=draft.subject_work_item_id,
            action=AuditAction.CREATED,
            outcome=AuditOutcome.SUCCESS,
            details={
                "anomaly_finding_id": str(finding.id),
                "kind": draft.kind,
                "layer": draft.layer,
                "severity": draft.severity,
                "score": str(draft.score),
            },
        )
        return UpsertResult(finding=finding, created=True, changed=True)

    if existing.input_digest == draft.input_digest:
        # Nothing about the inputs moved. No write, and — the half that costs
        # money — no usage event. This is the idempotency property §5.7 and
        # the gate both require.
        return UpsertResult(finding=existing, created=False, changed=False)

    existing.score = draft.score
    existing.severity = draft.severity
    existing.headline = draft.headline
    existing.metrics = dict(draft.metrics)
    existing.evidence = [dict(item) for item in draft.evidence]
    existing.input_digest = draft.input_digest
    existing.engine_version = vocab.ENGINE_VERSION
    existing.updated_at = datetime.now(timezone.utc)
    # STATUS IS NOT TOUCHED. A finding a person confirmed or dismissed keeps
    # their decision; only the numbers behind it refresh. Reopening it would
    # mean a reviewer's dismissal lasted until the next nightly run.
    db.flush()
    return UpsertResult(finding=existing, created=False, changed=True)


def emit_detected(
    db: Session, finding: AnomalyFinding, *, caused_by: Any = None
) -> None:
    """ARCH-13's `anomaly.detected` trigger, filterable by kind and severity.

    PUBLIC rather than INTERNAL, for the same reason `procurement.completed`
    is: a tenant's AP automation is the intended consumer. §5.2 is explicit
    that the radar does not block payment itself — blocking is an ARCH-13 rule
    a tenant writes on top ("if a duplicate finding above 90% exists, send to
    review"), and a rule cannot fire on an event it cannot see.

    The payload carries no evidence body. Evidence is a page of quoted
    document text, and pushing it through a webhook would send contract
    language to whatever URL a tenant configured. The event names the finding;
    the API serves the evidence under the capability gate.
    """
    outbox_service.emit(
        db,
        organization_id=finding.organization_id,
        workspace_id=finding.workspace_id,
        event_type=vocab.OUTBOX_EVENT_ANOMALY_DETECTED,
        resource_id=finding.id,
        payload={
            "finding_id": str(finding.id),
            "kind": finding.kind,
            "layer": finding.layer,
            "severity": finding.severity,
            "score": str(finding.score),
            "headline": finding.headline,
            "subject_work_item_id": str(finding.subject_work_item_id),
            "counterpart_work_item_id": (
                None
                if finding.counterpart_work_item_id is None
                else str(finding.counterpart_work_item_id)
            ),
        },
        idempotency_key=f"anomaly.detected:{finding.id}:{finding.input_digest}",
        caused_by=caused_by,
    )

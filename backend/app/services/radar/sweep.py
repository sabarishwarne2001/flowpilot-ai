"""ARCH-34 §5.5 — the sweep: candidates in, findings out, billed once.

WHAT MAKES THIS INCREMENTAL RATHER THAN A NIGHTLY FULL SCAN
===========================================================

Two things, and neither is a cache.

  * The COMPARISON is cheap by construction. A candidate pair is decided on
    128 stored integers and one 384-float dot product. No document text is
    read until a pair has already cleared a threshold, which is the entire
    reason MinHash is here rather than a diff.

  * The WRITE is keyed on `input_digest`. A re-run whose engine version,
    thresholds and compared inputs are unchanged finds the existing row,
    updates nothing, and — the half that costs money — emits no usage event.
    An idle workspace bills zero forever, which is §5.7's incentive argument
    turned into behaviour instead of a promise.

THE THREE DETECTORS RUN ON DIFFERENT CADENCES AND SHARE ONE PATH
================================================================

`anomaly.scan_document` runs duplicates for one document as it finishes
ingestion. `anomaly.nightly` runs price surge and contract drift across the
workspace, plus a duplicate sweep for anything that arrived while a worker was
down. Both land in `_write()`, so there is one place a finding is created and
one place the suppression lookup happens.

ONE WORKSPACE'S FAILURE DOES NOT STOP THE SWEEP
===============================================

Each workspace commits on its own, exactly as `procurement.score` does. A
tenant with a malformed extraction records a skip and the pass continues; the
alternative is one bad document stopping anomaly detection for every tenant,
which is the failure mode least likely to be noticed until somebody asks why
nothing has been flagged in a month.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import entitlements
from app.models.document_role import DocumentRole
from app.models.radar import AnomalyFinding, DocumentFingerprint
from app.models.work_item import WorkItem
from app.models.workspace import Workspace
from app.services import usage_service
from app.services.radar import candidates as candidates_module
from app.services.radar import drift as drift_module
from app.services.radar import findings as findings_module
from app.services.radar import fingerprint as fp
from app.services.radar import price_surge
from app.services.radar import suppressions as suppressions_module
from app.services.radar import vocabulary as vocab
from app.services.radar.layers import LayerSettings, all_hits, strongest

logger = logging.getLogger("app.services.radar.sweep")

__all__ = [
    "SweepOutcome",
    "settings_for",
    "fingerprint_document",
    "sweep_document",
    "sweep_workspace_prices",
    "sweep_workspace_drift",
    "DRIFT_BATCH",
]

#: How many contract pairs one nightly tick will align. Drift is the most
#: expensive detector per pair — an O(n·m) cosine alignment followed by up to
#: five parser runs on each aligned clause — so it is bounded separately from
#: the duplicate sweep.
DRIFT_BATCH: int = 25


@dataclass
class SweepOutcome:
    """What one sweep did, and whether it cost the tenant anything."""

    workspace_id: uuid.UUID
    compared: int = 0
    created: int = 0
    updated: int = 0
    suppressed: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def billable(self) -> bool:
        """A sweep that changed nothing is not billed.

        Note what is NOT here: `compared`. Comparing is what the sweep does
        whether or not anything is wrong, and billing for it would charge a
        clean workspace for being clean. The unit of value is a sweep that
        produced or refreshed a finding.
        """
        return (self.created + self.updated) > 0

    def as_payload(self) -> dict[str, Any]:
        return {
            "workspace_id": str(self.workspace_id),
            "compared": self.compared,
            "created": self.created,
            "updated": self.updated,
            "suppressed": self.suppressed,
            "unchanged": self.unchanged,
            "skipped": list(self.skipped),
        }


# ===========================================================================
# Settings
# ===========================================================================


def settings_for(db: Session, *, organization_id: uuid.UUID) -> LayerSettings:
    """Which layers this tenant's plan turns on.

    §5.7: Business receives duplicate detection through L2; Enterprise adds L3
    embedding search and contract drift. The decision is made ONCE, here, by
    the caller that can see the tier — not four times inside four layers each
    re-asking the same question of the same service.
    """
    from app.api import capability_gate

    has_radar = capability_gate.has_capability(
        db,
        organization_id=organization_id,
        capability_key=entitlements.ANOMALY_RADAR_CAPABILITY,
    )
    if not has_radar:
        return LayerSettings(enabled_layers=())

    # L3 is gated on the same capability today. The separation exists so that
    # a future tier split changes one tuple rather than every call site.
    return LayerSettings(enabled_layers=vocab.DUPLICATE_LAYER_ORDER)


def _workspace_currency(db: Session, workspace_id: uuid.UUID) -> str:
    currency = db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    ).scalar_one_or_none()
    return getattr(currency, "default_currency", None) or "INR"


def _label(item: Optional[WorkItem]) -> str:
    """What a document is called in a headline.

    The extracted document number if there is one, because that is what a
    finance person recognises; the filename otherwise, because that is what
    they uploaded. Never the UUID.
    """
    if item is None:
        return "another document"
    entities = item.extracted_entities or {}
    number = entities.get("document_number")
    if number:
        return str(number)
    return item.original_filename or "another document"


# ===========================================================================
# Fingerprinting
# ===========================================================================


def fingerprint_document(
    db: Session, *, work_item: WorkItem
) -> Optional[DocumentFingerprint]:
    """Compute and store one work item's fingerprint. Idempotent.

    Runs at ingest, inline on the existing extraction path, because it is
    cheap: a SHA copy, one shingling pass, 128 permutations, and a mean over
    vectors ARCH-11 already computed. The expensive half is the pairwise
    sweep, and that is the async job.
    """
    computed = candidates_module.build_fingerprint(db, work_item=work_item)
    if computed is None:
        logger.info(
            "radar.fingerprint_skipped",
            extra={"work_item_id": str(work_item.id), "reason": "no_chunks"},
        )
        return None

    row = db.execute(
        select(DocumentFingerprint).where(
            DocumentFingerprint.work_item_id == work_item.id
        )
    ).scalar_one_or_none()

    values = dict(
        organization_id=work_item.workspace.organization_id,
        workspace_id=work_item.workspace_id,
        content_sha256=computed.content_sha256,
        vendor_key=computed.vendor_key,
        document_number=computed.document_number,
        minhash=list(computed.minhash),
        shingle_count=computed.shingle_count,
        embedding=list(computed.embedding),
        embedding_model=computed.embedding_model,
        line_count=computed.line_count,
        page_count=computed.page_count,
        document_date=computed.document_date,
        total_micros=computed.total_micros,
        currency=computed.currency,
        engine_version=vocab.ENGINE_VERSION,
    )

    if row is None:
        row = DocumentFingerprint(work_item_id=work_item.id, **values)
        db.add(row)
    else:
        # UPDATE, never a second row. Two fingerprints for one document would
        # each be compared against every candidate, and the radar would report
        # the document as a duplicate of itself under a different id.
        for key, value in values.items():
            setattr(row, key, value)
        row.computed_at = datetime.now(timezone.utc)

    db.flush()
    return row


# ===========================================================================
# Duplicate sweep
# ===========================================================================


def _duplicate_digest(
    subject: fp.DocumentFingerprint,
    counterpart: fp.DocumentFingerprint,
    settings: LayerSettings,
    layer: str,
    score: Decimal,
) -> str:
    return fp.input_digest(
        {
            "layer": layer,
            "score": score,
            "subject": subject.content_sha256,
            "counterpart": counterpart.content_sha256,
            "subject_minhash": list(subject.minhash),
            "counterpart_minhash": list(counterpart.minhash),
            "settings": settings.as_digest_payload(),
        }
    )


def sweep_document(
    db: Session,
    *,
    work_item_id: uuid.UUID,
    settings: Optional[LayerSettings] = None,
) -> SweepOutcome:
    """Compare one document against its workspace. Duplicates only."""
    subject_row = db.execute(
        select(DocumentFingerprint).where(
            DocumentFingerprint.work_item_id == work_item_id
        )
    ).scalar_one_or_none()
    if subject_row is None:
        return SweepOutcome(
            workspace_id=uuid.UUID(int=0), skipped=["no_fingerprint"]
        )

    outcome = SweepOutcome(workspace_id=subject_row.workspace_id)
    resolved = settings or settings_for(
        db, organization_id=subject_row.organization_id
    )
    if not resolved.enabled_layers:
        outcome.skipped.append("capability_absent")
        return outcome

    subject = candidates_module.to_candidate(
        subject_row,
        chunks=candidates_module.load_chunks(db, work_item_id=work_item_id),
    )
    counterparts = candidates_module.load_candidates(
        db, workspace_id=subject_row.workspace_id, subject=subject_row
    )

    subject_item = db.execute(
        select(WorkItem).where(WorkItem.id == work_item_id)
    ).scalar_one_or_none()

    for counterpart in counterparts:
        outcome.compared += 1

        # Cheap pass first: signatures and mean vectors only, no text.
        hit = strongest(subject, counterpart, settings=resolved)
        if hit is None:
            continue

        counterpart_id = uuid.UUID(counterpart.work_item_id)

        if suppressions_module.is_suppressed(
            db,
            workspace_id=subject_row.workspace_id,
            layer=hit.layer,
            subject_work_item_id=work_item_id,
            counterpart_work_item_id=counterpart_id,
        ):
            outcome.suppressed += 1
            continue

        # Only now is text read, and only for the pair that already fired.
        if hit.layer == vocab.LAYER_L2:
            left_shingles, right_shingles = candidates_module.load_pair_shingles(
                db, left_id=work_item_id, right_id=counterpart_id
            )
            enriched_subject = candidates_module.to_candidate(
                subject_row, shingles=left_shingles, chunks=subject.chunks
            )
            enriched_counterpart = candidates_module.to_candidate(
                _fingerprint_row(db, counterpart_id) or subject_row,
                shingles=right_shingles,
            )
            hit = strongest(
                enriched_subject, enriched_counterpart, settings=resolved
            ) or hit
        elif hit.layer == vocab.LAYER_L3:
            enriched_counterpart = candidates_module.to_candidate(
                _fingerprint_row(db, counterpart_id) or subject_row,
                chunks=candidates_module.load_chunks(
                    db, work_item_id=counterpart_id
                ),
            )
            hit = strongest(subject, enriched_counterpart, settings=resolved) or hit

        # Corroboration: a second layer firing independently is the only thing
        # that lifts an L3 finding to HIGH. §5.9.
        corroborated = len(all_hits(subject, counterpart, settings=resolved)) > 1
        severity = vocab.severity_for(
            hit.layer, score=hit.score, corroborated=corroborated
        )

        counterpart_item = db.execute(
            select(WorkItem).where(WorkItem.id == counterpart_id)
        ).scalar_one_or_none()

        draft = findings_module.FindingDraft(
            organization_id=subject_row.organization_id,
            workspace_id=subject_row.workspace_id,
            layer=hit.layer,
            severity=severity,
            subject_work_item_id=work_item_id,
            counterpart_work_item_id=counterpart_id,
            score=hit.score,
            headline=findings_module.headline_for(
                layer=hit.layer,
                metrics=hit.metrics,
                subject_label=_label(subject_item),
                counterpart_label=_label(counterpart_item),
            ),
            metrics=dict(hit.metrics),
            evidence=hit.evidence,
            input_digest=_duplicate_digest(
                subject.fingerprint,
                counterpart.fingerprint,
                resolved,
                hit.layer,
                hit.score,
            ),
            dedupe_key=findings_module.dedupe_key_for(
                layer=hit.layer,
                subject_work_item_id=work_item_id,
                counterpart_work_item_id=counterpart_id,
            ),
        )
        _record(db, draft, outcome)

    return outcome


def _fingerprint_row(
    db: Session, work_item_id: uuid.UUID
) -> Optional[DocumentFingerprint]:
    return db.execute(
        select(DocumentFingerprint).where(
            DocumentFingerprint.work_item_id == work_item_id
        )
    ).scalar_one_or_none()


def _record(db: Session, draft: findings_module.FindingDraft, outcome: SweepOutcome) -> None:
    """Write one finding and count what happened to it."""
    result = findings_module.upsert(db, draft)
    if result.created:
        outcome.created += 1
        findings_module.emit_detected(db, result.finding)
    elif result.changed:
        outcome.updated += 1
        findings_module.emit_detected(db, result.finding)
    else:
        outcome.unchanged += 1


# ===========================================================================
# Price surge
# ===========================================================================


def sweep_workspace_prices(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    organization_id: uuid.UUID,
    as_of: Optional[date] = None,
    settings: Optional[price_surge.SurgeSettings] = None,
) -> SweepOutcome:
    """Every `(vendor_key, sku)` series in the workspace, newest price tested."""
    from app.models.procurement import ProcurementCase, ProcurementCaseLine

    outcome = SweepOutcome(workspace_id=workspace_id)
    resolved = settings or price_surge.DEFAULT_SETTINGS
    currency = _workspace_currency(db, workspace_id)
    anchor = as_of or datetime.now(timezone.utc).date()

    series_keys = db.execute(
        select(ProcurementCase.vendor_key, ProcurementCaseLine.sku)
        .join(ProcurementCase, ProcurementCaseLine.case_id == ProcurementCase.id)
        .where(
            ProcurementCaseLine.workspace_id == workspace_id,
            ProcurementCase.vendor_key.isnot(None),
            ProcurementCaseLine.sku.isnot(None),
            ProcurementCaseLine.invoice_unit_price_micros.isnot(None),
        )
        .distinct()
    ).all()

    for vendor_key, sku in series_keys:
        observations = candidates_module.series_for(
            db,
            workspace_id=workspace_id,
            vendor_key=vendor_key,
            sku=sku,
            currency=currency,
        )
        if len(observations) < resolved.min_observations + 1:
            continue

        latest = observations[-1]
        history = observations[:-1]
        outcome.compared += 1

        result = price_surge.evaluate(
            latest, history, as_of=anchor, settings=resolved
        )
        if not result.fired:
            continue

        if suppressions_module.is_suppressed(
            db,
            workspace_id=workspace_id,
            layer=vocab.LAYER_PRICE_SURGE,
            vendor_key=vendor_key,
            sku=sku,
        ):
            outcome.suppressed += 1
            continue

        subject_id = uuid.UUID(latest.work_item_id)
        subject_item = db.execute(
            select(WorkItem).where(WorkItem.id == subject_id)
        ).scalar_one_or_none()

        metrics = dict(result.metrics)
        metrics.update({"sku": sku, "vendor_key": vendor_key})

        draft = findings_module.FindingDraft(
            organization_id=organization_id,
            workspace_id=workspace_id,
            layer=vocab.LAYER_PRICE_SURGE,
            severity=vocab.severity_for(
                vocab.LAYER_PRICE_SURGE, score=result.score
            ),
            subject_work_item_id=subject_id,
            counterpart_work_item_id=None,
            score=result.score,
            headline=findings_module.headline_for(
                layer=vocab.LAYER_PRICE_SURGE,
                metrics=metrics,
                subject_label=_label(subject_item),
                counterpart_label="",
            ),
            metrics=metrics,
            evidence=result.evidence,
            input_digest=fp.input_digest(
                {
                    "layer": vocab.LAYER_PRICE_SURGE,
                    "vendor_key": vendor_key,
                    "sku": sku,
                    "observed": latest.unit_price_micros,
                    "observed_on": latest.observed_on,
                    "history": [item.unit_price_micros for item in history],
                    "settings": resolved.as_digest_payload(),
                }
            ),
            dedupe_key=findings_module.dedupe_key_for(
                layer=vocab.LAYER_PRICE_SURGE,
                subject_work_item_id=subject_id,
                vendor_key=vendor_key,
                sku=sku,
            ),
        )
        _record(db, draft, outcome)

    return outcome


# ===========================================================================
# Contract drift
# ===========================================================================


def sweep_workspace_drift(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    organization_id: uuid.UUID,
    settings: Optional[drift_module.DriftSettings] = None,
    limit: int = DRIFT_BATCH,
) -> SweepOutcome:
    """Compare contracts sharing a vendor key, pairwise, newest against each."""
    outcome = SweepOutcome(workspace_id=workspace_id)
    resolved = settings or drift_module.DEFAULT_SETTINGS
    currency = _workspace_currency(db, workspace_id)

    contracts = db.execute(
        select(DocumentFingerprint)
        .join(DocumentRole, DocumentRole.work_item_id == DocumentFingerprint.work_item_id)
        .where(
            DocumentFingerprint.workspace_id == workspace_id,
            DocumentFingerprint.vendor_key.isnot(None),
            DocumentRole.role == "CONTRACT",
        )
        .order_by(DocumentFingerprint.vendor_key, DocumentFingerprint.computed_at)
    ).scalars().all()

    by_vendor: dict[str, list[DocumentFingerprint]] = {}
    for row in contracts:
        by_vendor.setdefault(row.vendor_key or "", []).append(row)

    compared = 0
    for vendor_key, rows in by_vendor.items():
        if len(rows) < 2:
            continue
        # Newest against every earlier version. Comparing all pairs would be
        # quadratic and would report the same clause change once per pair;
        # the question a customer asks is "what changed in the latest one".
        newest = rows[-1]
        newest_chunks = candidates_module.load_chunks(
            db, work_item_id=newest.work_item_id
        )
        newest_item = db.execute(
            select(WorkItem).where(WorkItem.id == newest.work_item_id)
        ).scalar_one_or_none()

        for earlier in rows[:-1]:
            if compared >= limit:
                outcome.skipped.append("drift_batch_ceiling")
                return outcome
            compared += 1
            outcome.compared += 1

            if suppressions_module.is_suppressed(
                db,
                workspace_id=workspace_id,
                layer=vocab.LAYER_CONTRACT_DRIFT,
                subject_work_item_id=newest.work_item_id,
                counterpart_work_item_id=earlier.work_item_id,
            ):
                outcome.suppressed += 1
                continue

            earlier_chunks = candidates_module.load_chunks(
                db, work_item_id=earlier.work_item_id
            )
            readings = drift_module.evaluate(
                newest_chunks,
                earlier_chunks,
                settings=resolved,
                workspace_currency=currency,
            )
            changed = [
                reading
                for reading in readings
                if reading.status == vocab.DRIFT_CHANGED
            ]
            if not changed:
                continue

            earlier_item = db.execute(
                select(WorkItem).where(WorkItem.id == earlier.work_item_id)
            ).scalar_one_or_none()

            # One finding per contract pair, carrying every changed clause as
            # evidence. Not one per clause: "this contract disagrees with the
            # previous one in four places" is one thing a lawyer reads once,
            # and four rows is four dismissals.
            evidence: list[dict[str, Any]] = []
            for reading in changed:
                evidence.extend(dict(item) for item in reading.evidence)

            leading = changed[0]
            metrics = {
                "family": leading.family,
                "family_label": drift_module.FAMILY_LABELS[leading.family],
                "changed_clauses": len(changed),
                "alignment_min": str(resolved.alignment_min),
                "subject_value": _reading_value(leading, side="left"),
                "counterpart_value": _reading_value(leading, side="right"),
            }

            draft = findings_module.FindingDraft(
                organization_id=organization_id,
                workspace_id=workspace_id,
                layer=vocab.LAYER_CONTRACT_DRIFT,
                severity=vocab.severity_for(
                    vocab.LAYER_CONTRACT_DRIFT, score=leading.similarity
                ),
                subject_work_item_id=newest.work_item_id,
                counterpart_work_item_id=earlier.work_item_id,
                score=leading.similarity,
                headline=findings_module.headline_for(
                    layer=vocab.LAYER_CONTRACT_DRIFT,
                    metrics=metrics,
                    subject_label=_label(newest_item),
                    counterpart_label=_label(earlier_item),
                ),
                metrics=metrics,
                evidence=evidence[: vocab.MAX_EVIDENCE_ITEMS],
                input_digest=fp.input_digest(
                    {
                        "layer": vocab.LAYER_CONTRACT_DRIFT,
                        "subject": str(newest.work_item_id),
                        "counterpart": str(earlier.work_item_id),
                        "clauses": [
                            [
                                reading.family,
                                _reading_value(reading, side="left"),
                                _reading_value(reading, side="right"),
                            ]
                            for reading in changed
                        ],
                        "settings": resolved.as_digest_payload(),
                    }
                ),
                dedupe_key=findings_module.dedupe_key_for(
                    layer=vocab.LAYER_CONTRACT_DRIFT,
                    subject_work_item_id=newest.work_item_id,
                    counterpart_work_item_id=earlier.work_item_id,
                ),
            )
            _record(db, draft, outcome)

    return outcome


def _reading_value(reading: drift_module.DriftReading, *, side: str) -> str:
    source = reading.left if side == "left" else reading.right
    if source is None:
        return "?"
    if source.value is not None:
        return f"{source.value} {source.unit or ''}".strip()
    return source.literal or "?"


# ===========================================================================
# Metering
# ===========================================================================


def meter(
    db: Session,
    *,
    organization_id: uuid.UUID,
    outcome: SweepOutcome,
    input_digest: str,
    job_id: Optional[uuid.UUID] = None,
) -> None:
    """Emit `radar.sweep`, once, only when the sweep produced something.

    REQUEST, not PAGE: the cost is the assignment, like `procurement.case` and
    unlike `redaction.page`. A forty-page scan and a one-page letter cost the
    same MinHash comparison and the same pgvector probe.

    Keyed on `input_digest`, so a re-run over unchanged inputs is deduplicated
    by `usage_service` itself rather than by a check here that could be
    reordered away.
    """
    if not outcome.billable:
        return
    usage_service.record_usage(
        db,
        organization_id=organization_id,
        workspace_id=outcome.workspace_id,
        event_type=vocab.USAGE_EVENT_RADAR_SWEEP,
        quantity=1,
        provider="internal",
        job_id=job_id,
        idempotency_key=f"radar.sweep:{outcome.workspace_id}:{input_digest}",
        details=outcome.as_payload(),
    )

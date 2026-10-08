"""Processing batches: a named set of documents, its progress, its analytics and its dispatch.

Everything a batch shows is computed from the documents themselves at read time (status,
verification, fields); only the dispatch lanes are stored, because dispatching is an act with a
time and an author, and the tags it writes outlive the screen that wrote them.
"""

from __future__ import annotations

import statistics
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.batches import ProcessingBatch, ProcessingBatchItem
from app.models.ingestion import IngestionBatchItem
from app.models.work_item import WorkItem
from app.services.batches import canonical, confidence as conf, dispatch as dsp, healing
from app.services.batches import vocabulary as v


class BatchError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# ------------------------------------------------------------------ membership

def _workspace_ids(db: Session, workspace_id: uuid.UUID, ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return []
    found = set(
        db.execute(
            select(WorkItem.id).where(WorkItem.workspace_id == workspace_id, WorkItem.id.in_(wanted))
        ).scalars()
    )
    missing = [str(i) for i in wanted if i not in found]
    if missing:
        raise BatchError(
            "UNKNOWN_DOCUMENTS",
            f"{len(missing)} of the documents are not in this workspace.",
            status_code=404,
        )
    return wanted


def create_batch(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    organization_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    name: str,
    description: str = "",
    work_item_ids: Sequence[uuid.UUID] = (),
    ingestion_batch_id: Optional[uuid.UUID] = None,
) -> ProcessingBatch:
    name = " ".join((name or "").split())
    if not name:
        raise BatchError("NAME_REQUIRED", "Give the batch a name.")
    source = "SELECTION"
    ids: list[uuid.UUID] = []
    if ingestion_batch_id is not None:
        from app.models.ingestion import IngestionBatch

        upload = db.execute(
            select(IngestionBatch).where(
                IngestionBatch.id == ingestion_batch_id, IngestionBatch.workspace_id == workspace_id
            )
        ).scalar_one_or_none()
        if upload is None:
            raise BatchError("UNKNOWN_UPLOAD", "That upload is not in this workspace.", status_code=404)
        source = "INGESTION"
        ids = [
            i
            for i in db.execute(
                select(IngestionBatchItem.work_item_id)
                .where(
                    IngestionBatchItem.batch_id == ingestion_batch_id,
                    IngestionBatchItem.work_item_id.is_not(None),
                )
                .order_by(IngestionBatchItem.created_at)
            ).scalars()
            if i is not None
        ]
    ids = _workspace_ids(db, workspace_id, [*ids, *work_item_ids])
    if not ids:
        raise BatchError("NO_DOCUMENTS", "Choose at least one document for the batch.")
    if len(ids) > v.MAX_BATCH_DOCUMENTS:
        raise BatchError("TOO_MANY_DOCUMENTS", f"A batch holds at most {v.MAX_BATCH_DOCUMENTS} documents.")
    batch = ProcessingBatch(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        organization_id=organization_id,
        name=name[:120],
        description=(description or "").strip()[:2000],
        source=source,
        ingestion_batch_id=ingestion_batch_id,
        created_by_user_id=user_id,
    )
    db.add(batch)
    db.flush()
    for work_item_id in ids:
        db.add(ProcessingBatchItem(batch_id=batch.id, work_item_id=work_item_id, workspace_id=workspace_id))
    db.flush()
    return batch


def add_documents(db: Session, *, batch: ProcessingBatch, work_item_ids: Sequence[uuid.UUID]) -> int:
    ids = _workspace_ids(db, batch.workspace_id, work_item_ids)
    present = set(
        db.execute(
            select(ProcessingBatchItem.work_item_id).where(ProcessingBatchItem.batch_id == batch.id)
        ).scalars()
    )
    new = [i for i in ids if i not in present]
    if len(present) + len(new) > v.MAX_BATCH_DOCUMENTS:
        raise BatchError("TOO_MANY_DOCUMENTS", f"A batch holds at most {v.MAX_BATCH_DOCUMENTS} documents.")
    for work_item_id in new:
        db.add(ProcessingBatchItem(batch_id=batch.id, work_item_id=work_item_id, workspace_id=batch.workspace_id))
    batch.updated_at = datetime.now(timezone.utc)
    db.flush()
    return len(new)


def remove_document(db: Session, *, batch: ProcessingBatch, work_item_id: uuid.UUID) -> bool:
    item = db.get(ProcessingBatchItem, (batch.id, work_item_id))
    if item is None:
        return False
    db.delete(item)
    batch.updated_at = datetime.now(timezone.utc)
    db.flush()
    return True


# ------------------------------------------------------------------ progress (list view)

@dataclass(frozen=True)
class Progress:
    documents: int = 0
    queued: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    straight_through: int = 0
    review: int = 0
    exception: int = 0
    mean_confidence: Optional[float] = None

    @property
    def finished(self) -> int:
        return self.completed + self.failed

    @property
    def percent(self) -> float:
        return round(self.finished / self.documents * 100, 1) if self.documents else 0.0


def progress_for(db: Session, batch_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Progress]:
    """Document counts by status and lane, and mean confidence, for several batches in two queries."""
    if not batch_ids:
        return {}
    rows = db.execute(
        select(
            ProcessingBatchItem.batch_id,
            func.count(),
            func.count().filter(WorkItem.status == "QUEUED"),
            func.count().filter(WorkItem.status == "PROCESSING"),
            func.count().filter(WorkItem.status == "COMPLETED"),
            func.count().filter(WorkItem.status == "FAILED"),
            func.count().filter(ProcessingBatchItem.lane == v.LANE_STRAIGHT_THROUGH),
            func.count().filter(ProcessingBatchItem.lane == v.LANE_REVIEW),
            func.count().filter(ProcessingBatchItem.lane == v.LANE_EXCEPTION),
        )
        .join(WorkItem, WorkItem.id == ProcessingBatchItem.work_item_id)
        .where(ProcessingBatchItem.batch_id.in_(list(batch_ids)))
        .group_by(ProcessingBatchItem.batch_id)
    ).all()
    from app.models.verification import DocumentVerification

    latest = (
        select(DocumentVerification.work_item_id, DocumentVerification.confidence)
        .order_by(DocumentVerification.work_item_id, DocumentVerification.created_at.desc())
        .distinct(DocumentVerification.work_item_id)
        .subquery()
    )
    means = dict(
        db.execute(
            select(ProcessingBatchItem.batch_id, func.avg(latest.c.confidence))
            .join(latest, latest.c.work_item_id == ProcessingBatchItem.work_item_id)
            .where(ProcessingBatchItem.batch_id.in_(list(batch_ids)))
            .group_by(ProcessingBatchItem.batch_id)
        ).all()
    )
    out: dict[uuid.UUID, Progress] = {}
    for batch_id, total, queued, processing, completed, failed, st, review, exception in rows:
        mean = means.get(batch_id)
        out[batch_id] = Progress(
            int(total), int(queued), int(processing), int(completed), int(failed),
            int(st), int(review), int(exception),
            round(float(mean), 4) if mean is not None else None,
        )
    return out


# ------------------------------------------------------------------ assessment (detail view)

@dataclass
class DocumentAssessment:
    item: WorkItem
    membership: ProcessingBatchItem
    confidence: Optional[conf.DocumentConfidence]
    healing: healing.HealingPlan
    decision: dsp.Decision


@dataclass
class Assessment:
    batch: ProcessingBatch
    policy: dsp.Policy
    documents: list[DocumentAssessment] = field(default_factory=list)


def assess(db: Session, *, batch: ProcessingBatch) -> Assessment:
    """Every document of the batch with its confidence, schema health and the lane it would go to."""
    rows = db.execute(
        select(ProcessingBatchItem, WorkItem)
        .join(WorkItem, WorkItem.id == ProcessingBatchItem.work_item_id)
        .where(ProcessingBatchItem.batch_id == batch.id)
        .order_by(ProcessingBatchItem.added_at, WorkItem.original_filename, WorkItem.id)
    ).all()
    policy = dsp.policy_for(db, batch.workspace_id)
    scores = conf.for_documents(db, [item.id for _, item in rows])
    schemas: dict[str, Optional[canonical.CanonicalSchema]] = {}
    result = Assessment(batch=batch, policy=policy)
    for membership, item in rows:
        entities = item.extracted_entities if isinstance(item.extracted_entities, dict) else {}
        schema = canonical.schema_for(
            db,
            workspace_id=batch.workspace_id,
            organization_id=batch.organization_id,
            document_type=canonical.document_type_of(entities),
            _cache=schemas,
        ) if item.status == "COMPLETED" else None
        plan = healing.plan(entities, schema)
        decision = dsp.decide(
            status=item.status,
            failure_reason=item.failure_reason,
            entities=entities,
            confidence=scores.get(item.id),
            healing=plan,
            policy=policy,
        )
        result.documents.append(DocumentAssessment(item, membership, scores.get(item.id), plan, decision))
    return result


# ------------------------------------------------------------------ analytics

def _histogram(values: Sequence[float]) -> list[dict[str, Any]]:
    buckets = []
    for index, (label, low, high) in enumerate(v.CONFIDENCE_BUCKETS):
        last = index == len(v.CONFIDENCE_BUCKETS) - 1
        count = sum(1 for x in values if low <= x < high or (last and x == high))
        buckets.append({"label": label, "low": low, "high": high, "count": count})
    return buckets


def _seconds(item: WorkItem) -> Optional[float]:
    end = item.stage_updated_at or item.updated_at
    if item.status != "COMPLETED" or end is None or item.created_at is None:
        return None
    seconds = (end - item.created_at).total_seconds()
    return seconds if seconds >= 0 else None


def analytics(assessment: Assessment) -> dict[str, Any]:
    docs = assessment.documents
    statuses = Counter(d.item.status for d in docs)
    scored = [d.confidence.confidence for d in docs if d.confidence and d.confidence.confidence is not None]
    field_stats: dict[str, list[conf.FieldConfidence]] = defaultdict(list)
    for d in docs:
        for f in (d.confidence.fields if d.confidence else ()):
            field_stats[f.field].append(f)
    missing: Counter[str] = Counter()
    for d in docs:
        missing.update(d.healing.missing_required)
    fields = []
    for name, entries in field_stats.items():
        values = [e.confidence for e in entries]
        fields.append({
            "field": name,
            "documents": len(entries),
            "mean_confidence": round(statistics.fmean(values), 4),
            "min_confidence": round(min(values), 4),
            "disagreement_rate": round(sum(1 for e in entries if not e.agreed) / len(entries), 4),
            "missing_required": missing.get(name, 0),
        })
    for name, count in missing.items():
        if name not in field_stats:
            fields.append({
                "field": name, "documents": 0, "mean_confidence": None, "min_confidence": None,
                "disagreement_rate": None, "missing_required": count,
            })
    fields.sort(key=lambda f: (f["mean_confidence"] is None, f["mean_confidence"] or 0, -f["missing_required"]))

    by_type: dict[str, list[DocumentAssessment]] = defaultdict(list)
    for d in docs:
        if d.item.status == "COMPLETED":
            by_type[d.healing.document_type or "Unclassified"].append(d)
    types = []
    for name, group in sorted(by_type.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        group_scores = [g.confidence.confidence for g in group if g.confidence and g.confidence.confidence is not None]
        lanes = Counter(g.decision.lane for g in group)
        types.append({
            "document_type": name,
            "documents": len(group),
            "mean_confidence": round(statistics.fmean(group_scores), 4) if group_scores else None,
            "straight_through": lanes.get(v.LANE_STRAIGHT_THROUGH, 0),
            "review": lanes.get(v.LANE_REVIEW, 0),
            "exception": lanes.get(v.LANE_EXCEPTION, 0),
        })

    lanes = Counter(d.decision.lane for d in docs)
    decided = sum(lanes.get(lane, 0) for lane in v.LANES)
    states = Counter(d.healing.state for d in docs if d.item.status == "COMPLETED")
    durations = sorted(s for s in (_seconds(d.item) for d in docs) if s is not None)
    p95 = durations[min(len(durations) - 1, int(round(0.95 * (len(durations) - 1))))] if durations else None
    window = None
    completed_at = [d.item.stage_updated_at or d.item.updated_at for d in docs if d.item.status == "COMPLETED"]
    created = [d.item.created_at for d in docs if d.item.created_at is not None]
    if completed_at and created:
        window = max((max(completed_at) - min(created)).total_seconds(), 1.0)
    return {
        "documents": len(docs),
        "status": {
            "queued": statuses.get("QUEUED", 0),
            "processing": statuses.get("PROCESSING", 0),
            "completed": statuses.get("COMPLETED", 0),
            "failed": statuses.get("FAILED", 0),
        },
        "confidence": {
            "scored": len(scored),
            "unscored": sum(1 for d in docs if d.item.status == "COMPLETED") - len(scored),
            "mean": round(statistics.fmean(scored), 4) if scored else None,
            "median": round(statistics.median(scored), 4) if scored else None,
            "histogram": _histogram(scored),
            "field_histogram": _histogram([f.confidence for entries in field_stats.values() for f in entries]),
        },
        "fields": fields[:50],
        "document_types": types,
        "lanes": {
            "straight_through": lanes.get(v.LANE_STRAIGHT_THROUGH, 0),
            "review": lanes.get(v.LANE_REVIEW, 0),
            "exception": lanes.get(v.LANE_EXCEPTION, 0),
            "pending": lanes.get(None, 0),
        },
        "straight_through_rate": round(lanes.get(v.LANE_STRAIGHT_THROUGH, 0) / decided, 4) if decided else None,
        "schema": {
            "healthy": states.get("HEALTHY", 0),
            "healable": states.get("HEALABLE", 0),
            "needs_attention": states.get("NEEDS_ATTENTION", 0),
            "no_schema": states.get("NO_SCHEMA", 0),
            "missing_required": sum(missing.values()),
        },
        "throughput": {
            "median_seconds": round(statistics.median(durations), 1) if durations else None,
            "p95_seconds": round(p95, 1) if p95 is not None else None,
            "documents_per_hour": round(len(completed_at) / window * 3600, 1) if window else None,
        },
    }


# ------------------------------------------------------------------ acts

def dispatch(
    db: Session,
    *,
    batch: ProcessingBatch,
    user_id: Optional[uuid.UUID],
) -> dict[str, Any]:
    """Record each document's lane (and tag it, if the policy says so). Re-running is safe."""
    assessment = assess(db, batch=batch)
    now = datetime.now(timezone.utc)
    lanes: dict[uuid.UUID, Optional[str]] = {}
    for d in assessment.documents:
        membership = d.membership
        membership.lane = d.decision.lane
        membership.lane_reasons = list(d.decision.reasons)
        membership.confidence = (
            Decimal(str(round(d.decision.confidence, 4))) if d.decision.confidence is not None else None
        )
        membership.dispatched_at = now if d.decision.lane is not None else None
        lanes[d.item.id] = d.decision.lane
    if assessment.policy.tag_documents:
        dsp.tag_documents(db, workspace_id=batch.workspace_id, lanes=lanes, user_id=user_id)
    batch.dispatched_at = now
    batch.updated_at = now
    db.flush()
    counts = Counter(lanes.values())
    return {
        "dispatched_at": now,
        "straight_through": counts.get(v.LANE_STRAIGHT_THROUGH, 0),
        "review": counts.get(v.LANE_REVIEW, 0),
        "exception": counts.get(v.LANE_EXCEPTION, 0),
        "pending": counts.get(None, 0),
        "tagged": assessment.policy.tag_documents,
    }


def heal(
    db: Session,
    *,
    batch: ProcessingBatch,
    user_id: Optional[uuid.UUID],
    work_item_ids: Optional[Sequence[uuid.UUID]] = None,
) -> dict[str, Any]:
    """Apply schema healing to the batch's documents (or the ones named). One outcome per document."""
    assessment = assess(db, batch=batch)
    wanted = set(work_item_ids) if work_item_ids else None
    results: list[dict[str, Any]] = []
    healed = refused = skipped = 0
    for d in assessment.documents:
        if wanted is not None and d.item.id not in wanted:
            continue
        if d.healing.state in ("HEALTHY", "NO_SCHEMA") or not d.healing.changes:
            skipped += 1
            continue
        try:
            with db.begin_nested():
                event = healing.apply(
                    db,
                    work_item=d.item,
                    healing=d.healing,
                    organization_id=batch.organization_id,
                    user_id=user_id,
                    batch_id=batch.id,
                )
            healed += 1
            results.append({
                "work_item_id": str(d.item.id), "outcome": "healed", "event_id": str(event.id),
                "changes": len(d.healing.changes),
            })
        except healing.HealingRefused as exc:
            refused += 1
            results.append({
                "work_item_id": str(d.item.id), "outcome": "refused", "code": exc.code, "message": exc.message,
            })
    return {"healed": healed, "refused": refused, "skipped": skipped, "results": results}


def retry_failed(db: Session, *, batch: ProcessingBatch) -> dict[str, Any]:
    from app.services.ingestion import bulk_service

    failed = list(
        db.execute(
            select(WorkItem.id)
            .join(ProcessingBatchItem, ProcessingBatchItem.work_item_id == WorkItem.id)
            .where(ProcessingBatchItem.batch_id == batch.id, WorkItem.status == "FAILED")
        ).scalars()
    )
    if not failed:
        return {"requeued": 0, "refused": 0, "results": []}
    results = bulk_service.bulk_reprocess(
        db, organization_id=batch.organization_id, workspace_id=batch.workspace_id, ids=failed
    )
    db.flush()
    return {
        "requeued": sum(1 for r in results if r.outcome == "ok"),
        "refused": sum(1 for r in results if r.outcome != "ok"),
        "results": [r.as_dict() for r in results],
    }


def lane_order() -> Any:
    """SQL ordering that lists exceptions, then reviews, then straight-through, then pending."""
    return case(
        (ProcessingBatchItem.lane == v.LANE_EXCEPTION, 0),
        (ProcessingBatchItem.lane == v.LANE_REVIEW, 1),
        (ProcessingBatchItem.lane == v.LANE_STRAIGHT_THROUGH, 2),
        else_=3,
    )


__all__ = [
    "Assessment",
    "BatchError",
    "DocumentAssessment",
    "Progress",
    "add_documents",
    "analytics",
    "assess",
    "create_batch",
    "dispatch",
    "heal",
    "lane_order",
    "progress_for",
    "remove_document",
    "retry_failed",
]

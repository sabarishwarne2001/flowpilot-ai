"""Dispatch: where each finished document goes next, and why, in words a reviewer can act on.

  EXCEPTION         processing failed, nothing was extracted, or confidence is below the floor
  REVIEW            a person should look: the review queue holds it, a required field is missing,
                    a value is unreadable, confidence is under the straight-through threshold, or it
                    was never verified (no score is not a high score)
  STRAIGHT_THROUGH  verified at or above the threshold, released by verification, schema complete

The thresholds are the workspace's dispatch policy (defaults 90% and 60%). Dispatching records the
lane on the batch item and, when the policy says so, tags the document (`dispatch-review`, ...) so
the Documents list and automation rules can act on it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.batches import DispatchPolicy
from app.services.batches import vocabulary as v
from app.services.batches.confidence import DocumentConfidence
from app.services.batches.healing import RESERVED_KEYS, HealingPlan


@dataclass(frozen=True)
class Policy:
    straight_through_min: Decimal
    review_min: Decimal
    require_required_fields: bool
    tag_documents: bool
    is_default: bool
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class Decision:
    #: None while the document is still processing: it is not dispatched yet.
    lane: Optional[str]
    reasons: tuple[str, ...]
    confidence: Optional[float]


def policy_for(db: Session, workspace_id: uuid.UUID) -> Policy:
    row = db.get(DispatchPolicy, workspace_id)
    if row is None:
        return Policy(v.DEFAULT_STRAIGHT_THROUGH_MIN, v.DEFAULT_REVIEW_MIN, True, True, True)
    return Policy(
        Decimal(row.straight_through_min_confidence),
        Decimal(row.review_min_confidence),
        bool(row.require_required_fields),
        bool(row.tag_documents),
        False,
        row.updated_at,
    )


def _pct(value: float | Decimal) -> str:
    return f"{float(value) * 100:.0f}%"


def _has_fields(entities: Any) -> bool:
    if not isinstance(entities, dict):
        return False
    return any(
        key not in RESERVED_KEYS and value not in (None, "", [], {}) for key, value in entities.items()
    )


def decide(
    *,
    status: str,
    failure_reason: Optional[str],
    entities: Any,
    confidence: Optional[DocumentConfidence],
    healing: HealingPlan,
    policy: Policy,
) -> Decision:
    score = confidence.confidence if confidence is not None else None
    if status == "FAILED":
        detail = (failure_reason or "").strip() or "no reason was recorded"
        return Decision(v.LANE_EXCEPTION, (f"Processing failed: {detail}",), score)
    if status != "COMPLETED":
        return Decision(None, ("Still processing.",), score)
    if not _has_fields(entities):
        return Decision(v.LANE_EXCEPTION, ("Nothing was extracted from it.",), score)

    exception: list[str] = []
    review: list[str] = []
    if score is not None and Decimal(str(score)) < policy.review_min:
        exception.append(f"Confidence {_pct(score)} is below the {_pct(policy.review_min)} floor.")
    for issue in healing.issues:
        if issue.kind in ("UNPARSEABLE", "AMBIGUOUS_DATE", "CONFLICT"):
            review.append(issue.message)
    if policy.require_required_fields and healing.missing_required:
        names = ", ".join(healing.missing_required)
        review.append(f"Required field{'s' if len(healing.missing_required) != 1 else ''} missing: {names}.")
    if confidence is None or score is None:
        review.append("It was not verified, so it has no confidence score.")
    elif confidence.blocking:
        review.append(confidence.reason or "It is waiting in the review queue.")
    elif Decimal(str(score)) < policy.straight_through_min:
        review.append(
            f"Confidence {_pct(score)} is below the {_pct(policy.straight_through_min)} straight-through threshold."
        )

    if exception:
        return Decision(v.LANE_EXCEPTION, tuple(exception + review), score)
    if review:
        return Decision(v.LANE_REVIEW, tuple(review), score)
    complete = " and every required field is present" if healing.required_total else ""
    return Decision(v.LANE_STRAIGHT_THROUGH, (f"Confidence {_pct(score or 0)}{complete}.",), score)


def tag_documents(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    lanes: dict[uuid.UUID, Optional[str]],
    user_id: Optional[uuid.UUID],
) -> None:
    """One `dispatch-*` tag per dispatched document; an undispatched one loses its old tag."""
    from app.models.ingestion import WorkItemTag

    ids = list(lanes)
    if not ids:
        return
    db.execute(
        delete(WorkItemTag).where(
            WorkItemTag.workspace_id == workspace_id,
            WorkItemTag.work_item_id.in_(ids),
            WorkItemTag.tag.in_(list(v.LANE_TAGS.values())),
        )
    )
    for work_item_id, lane in lanes.items():
        if lane is None:
            continue
        db.add(
            WorkItemTag(
                work_item_id=work_item_id,
                workspace_id=workspace_id,
                tag=v.LANE_TAGS[lane],
                created_by_user_id=user_id,
            )
        )
    db.flush()


def save_policy(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    straight_through_min: Decimal,
    review_min: Decimal,
    require_required_fields: bool,
    tag_documents: bool,
    user_id: Optional[uuid.UUID],
) -> DispatchPolicy:
    row = db.get(DispatchPolicy, workspace_id)
    if row is None:
        row = DispatchPolicy(workspace_id=workspace_id)
        db.add(row)
    row.straight_through_min_confidence = straight_through_min
    row.review_min_confidence = review_min
    row.require_required_fields = require_required_fields
    row.tag_documents = tag_documents
    row.updated_by_user_id = user_id
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def existing_tags(db: Session, work_item_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    from app.models.ingestion import WorkItemTag

    if not work_item_ids:
        return {}
    rows = db.execute(
        select(WorkItemTag.work_item_id, WorkItemTag.tag).where(
            WorkItemTag.work_item_id.in_(work_item_ids), WorkItemTag.tag.in_(list(v.LANE_TAGS.values()))
        )
    ).all()
    return {work_item_id: tag for work_item_id, tag in rows}


__all__ = ["Decision", "Policy", "decide", "existing_tags", "policy_for", "save_policy", "tag_documents"]

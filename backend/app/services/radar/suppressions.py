"""ARCH-34 §5.4 — "this is fine, and here is why", recorded and honoured.

WHY A DISMISSAL WRITES A ROW INSTEAD OF DELETING ONE
====================================================

The finding stays. A dismissed finding is the record that the engine raised
something and a named person judged it, and the question asked after an
invoice is paid twice is exactly "did the system see this, and what did we
do". Deleting the finding erases both halves of that answer.

So dismissal does two things: it closes the finding with a mandatory reason,
and it writes a suppression that stops the same claim resurfacing. Those are
different records with different lifetimes — the finding is history, the
suppression is policy.

SUPPRESSION IS SCOPED TO THE LAYER, NOT THE PAIR
================================================

`(workspace, layer, LEAST(a,b), GREATEST(a,b))`.

A reviewer who says "these two similar quotes are not duplicates" has made a
statement about an L3 embedding guess. They have not said "and never tell me
if somebody uploads the exact same file twice". Suppressing across all layers
would convert one reasonable judgement into a permanent blind spot on the
strongest signal the radar has, and the person who granted it would have no
idea they had.

`is_suppressed()` is therefore asked with a layer, and the sweep asks it for
the layer that actually fired.

EXPIRY IS EXPRESSIBLE AND OPTIONAL
==================================

`expires_at IS NULL` means never. That is deliberately available, because some
suppressions are structural — a supplier who legitimately resets their invoice
series every April will collide every April. Everything else should expire,
and the API defaults to a year.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.radar import AnomalySuppression
from app.services.radar import vocabulary as vocab

logger = logging.getLogger("app.services.radar.suppressions")

__all__ = [
    "DEFAULT_TTL_DAYS",
    "is_suppressed",
    "suppress_pair",
    "suppress_series",
    "active_for_workspace",
]

#: A year. Long enough that an annual cycle is covered once, short enough that
#: a blind spot granted today is reviewed rather than inherited.
DEFAULT_TTL_DAYS: int = 365


def _unexpired(now: datetime):
    return or_(
        AnomalySuppression.expires_at.is_(None),
        AnomalySuppression.expires_at > now,
    )


def is_suppressed(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    layer: str,
    subject_work_item_id: Optional[uuid.UUID] = None,
    counterpart_work_item_id: Optional[uuid.UUID] = None,
    vendor_key: Optional[str] = None,
    sku: Optional[str] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Whether this exact claim has already been judged fine by a person.

    Called by the sweep BEFORE writing. Skipping this lookup is one of the
    mutants `verify_arch34.py` requires to die, and it is a mutant that would
    look harmless in review: everything still works, findings still appear,
    and every dismissal a customer ever made quietly stops meaning anything.
    """
    moment = now or datetime.now(timezone.utc)

    if layer in vocab.PAIRWISE_LAYERS:
        if subject_work_item_id is None or counterpart_work_item_id is None:
            return False
        a, b = sorted([subject_work_item_id, counterpart_work_item_id], key=str)
        stmt = select(AnomalySuppression.id).where(
            AnomalySuppression.workspace_id == workspace_id,
            AnomalySuppression.layer == layer,
            _unexpired(moment),
            or_(
                and_(
                    AnomalySuppression.item_a_id == a,
                    AnomalySuppression.item_b_id == b,
                ),
                and_(
                    AnomalySuppression.item_a_id == b,
                    AnomalySuppression.item_b_id == a,
                ),
            ),
        )
    else:
        if not vendor_key:
            return False
        stmt = select(AnomalySuppression.id).where(
            AnomalySuppression.workspace_id == workspace_id,
            AnomalySuppression.layer == layer,
            AnomalySuppression.vendor_key == vendor_key,
            AnomalySuppression.sku == (sku or None),
            AnomalySuppression.item_b_id.is_(None),
            _unexpired(moment),
        )

    return db.execute(stmt.limit(1)).scalar_one_or_none() is not None


def suppress_pair(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    layer: str,
    item_a_id: uuid.UUID,
    item_b_id: uuid.UUID,
    reason: str,
    created_by_user_id: uuid.UUID,
    ttl_days: Optional[int] = DEFAULT_TTL_DAYS,
) -> AnomalySuppression:
    """Record that a duplicate or drift pair is not a problem, at this layer."""
    body = (reason or "").strip()
    if not body:
        raise ValueError(
            "A suppression needs a reason. 'Not an anomaly' with no reason is "
            "indistinguishable from a misclick six months later, and the next "
            "reviewer inherits a silence."
        )
    if layer not in vocab.PAIRWISE_LAYERS:
        raise ValueError(
            f"{layer} identifies a series, not a pair. Use suppress_series."
        )

    a, b = sorted([item_a_id, item_b_id], key=str)
    existing = db.execute(
        select(AnomalySuppression).where(
            AnomalySuppression.workspace_id == workspace_id,
            AnomalySuppression.layer == layer,
            AnomalySuppression.item_a_id == a,
            AnomalySuppression.item_b_id == b,
        )
    ).scalar_one_or_none()

    expires = (
        None
        if ttl_days is None
        else datetime.now(timezone.utc) + timedelta(days=ttl_days)
    )

    if existing is not None:
        # Re-dismissing refreshes the reason and the clock rather than failing
        # on `uq_as_pair_layer`. A reviewer saying it again is a stronger
        # signal than a 409.
        existing.reason = body
        existing.created_by_user_id = created_by_user_id
        existing.expires_at = expires
        db.flush()
        return existing

    row = AnomalySuppression(
        organization_id=organization_id,
        workspace_id=workspace_id,
        kind=vocab.kind_for_layer(layer),
        layer=layer,
        item_a_id=a,
        item_b_id=b,
        reason=body,
        created_by_user_id=created_by_user_id,
        expires_at=expires,
    )
    db.add(row)
    db.flush()
    return row


def suppress_series(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    item_a_id: uuid.UUID,
    vendor_key: str,
    sku: Optional[str],
    reason: str,
    created_by_user_id: uuid.UUID,
    ttl_days: Optional[int] = DEFAULT_TTL_DAYS,
) -> AnomalySuppression:
    """Record that a price series' movement is expected."""
    body = (reason or "").strip()
    if not body:
        raise ValueError("A suppression needs a reason.")
    if not vendor_key:
        raise ValueError(
            "A series suppression needs a vendor key, or it would silence the "
            "same SKU across every supplier in the workspace."
        )

    existing = db.execute(
        select(AnomalySuppression).where(
            AnomalySuppression.workspace_id == workspace_id,
            AnomalySuppression.layer == vocab.LAYER_PRICE_SURGE,
            AnomalySuppression.vendor_key == vendor_key,
            AnomalySuppression.sku == (sku or None),
            AnomalySuppression.item_b_id.is_(None),
        )
    ).scalar_one_or_none()

    expires = (
        None
        if ttl_days is None
        else datetime.now(timezone.utc) + timedelta(days=ttl_days)
    )

    if existing is not None:
        existing.reason = body
        existing.created_by_user_id = created_by_user_id
        existing.expires_at = expires
        db.flush()
        return existing

    row = AnomalySuppression(
        organization_id=organization_id,
        workspace_id=workspace_id,
        kind=vocab.KIND_PRICE_SURGE,
        layer=vocab.LAYER_PRICE_SURGE,
        item_a_id=item_a_id,
        item_b_id=None,
        vendor_key=vendor_key,
        sku=sku or None,
        reason=body,
        created_by_user_id=created_by_user_id,
        expires_at=expires,
    )
    db.add(row)
    db.flush()
    return row


def active_for_workspace(
    db: Session, *, workspace_id: uuid.UUID, now: Optional[datetime] = None
) -> list[AnomalySuppression]:
    """Every suppression still in force. For the console's settings view."""
    moment = now or datetime.now(timezone.utc)
    return list(
        db.execute(
            select(AnomalySuppression)
            .where(
                AnomalySuppression.workspace_id == workspace_id,
                _unexpired(moment),
            )
            .order_by(AnomalySuppression.created_at.desc())
        ).scalars()
    )

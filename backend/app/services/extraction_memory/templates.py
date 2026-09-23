"""ARCH41-S2:templates — layout clusters, assigned incrementally, merged nightly."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.extraction_memory import (
    ExtractionAnchorRule,
    ExtractionExemplar,
    ExtractionMemoryApplication,
    ExtractionMemoryTrial,
    ExtractionTemplate,
    ExtractionTemplateMember,
)
from app.services.extraction_memory import vocabulary as v
from app.services.extraction_memory.fingerprint import Fingerprint, cosine


def document_type_of(work_item: Any) -> str:
    entities = getattr(work_item, "extracted_entities", None) or {}
    value = entities.get("document_classification")
    if not value:
        value = (entities.get("classification_details") or {}).get("document_classification")
    text = str(value or "Other").strip() or "Other"
    return text[:64]


def assign(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    work_item_id: uuid.UUID,
    document_type: str,
    fp: Fingerprint,
) -> tuple[ExtractionTemplate, float]:
    """The template this document belongs to, creating one for a new layout."""
    member = db.get(ExtractionTemplateMember, work_item_id)
    if member is not None:
        template = db.get(ExtractionTemplate, member.template_id)
        if template is not None:
            return template, float(member.similarity)

    distance = ExtractionTemplate.signature.cosine_distance(fp.vector)
    nearest = db.execute(
        select(ExtractionTemplate, distance.label("distance"))
        .where(
            ExtractionTemplate.workspace_id == workspace_id,
            ExtractionTemplate.document_type == document_type,
        )
        .order_by(distance)
        .limit(1)
    ).first()

    if nearest is not None and 1.0 - float(nearest.distance) >= v.TEMPLATE_MATCH_THRESHOLD:
        template, similarity = nearest[0], 1.0 - float(nearest.distance)
    else:
        template = ExtractionTemplate(
            id=uuid.uuid4(),
            organization_id=organization_id,
            workspace_id=workspace_id,
            document_type=document_type,
            signature=fp.vector,
            anchor_tokens=fp.anchor_tokens,
            member_count=0,
            state=v.TEMPLATE_LEARNING,
        )
        db.add(template)
        db.flush([template])
        similarity = 1.0

    similarity = max(0.0, min(1.0, similarity))
    db.add(
        ExtractionTemplateMember(
            work_item_id=work_item_id,
            workspace_id=workspace_id,
            template_id=template.id,
            similarity=Decimal(str(round(similarity, 4))),
        )
    )
    template.member_count = (template.member_count or 0) + 1
    db.flush()
    return template, similarity


def merge_similar(db: Session, *, workspace_id: uuid.UUID) -> int:
    """Fold a LEARNING layout into a larger one it has since converged with.

    Only a LEARNING template with no trial is ever folded: moving a template
    that is mid-trial would mix two populations in one comparison. Its rules
    are dropped rather than moved — the nightly learn step rebuilds them on the
    merged evidence, which is the point of merging.
    """
    merged = 0
    templates = list(
        db.execute(
            select(ExtractionTemplate)
            .where(ExtractionTemplate.workspace_id == workspace_id)
            .order_by(ExtractionTemplate.document_type, ExtractionTemplate.member_count.desc())
        ).scalars()
    )
    gone: set[uuid.UUID] = set()
    for i, keep in enumerate(templates):
        if keep.id in gone:
            continue
        for loser in templates[i + 1:]:
            if loser.id in gone or loser.document_type != keep.document_type:
                continue
            if loser.state != v.TEMPLATE_LEARNING:
                continue
            has_trial = db.execute(
                select(ExtractionMemoryTrial.id).where(ExtractionMemoryTrial.template_id == loser.id).limit(1)
            ).first()
            if has_trial is not None:
                continue
            if cosine(list(keep.signature), list(loser.signature)) < v.TEMPLATE_MATCH_THRESHOLD:
                continue
            for model in (ExtractionTemplateMember, ExtractionExemplar, ExtractionMemoryApplication):
                db.execute(update(model).where(model.template_id == loser.id).values(template_id=keep.id))
            db.execute(delete(ExtractionAnchorRule).where(ExtractionAnchorRule.template_id == loser.id))
            keep.member_count = (keep.member_count or 0) + (loser.member_count or 0)
            db.flush()
            db.delete(loser)
            db.flush()
            gone.add(loser.id)
            merged += 1
    return merged


def get(db: Session, template_id: Optional[uuid.UUID]) -> Optional[ExtractionTemplate]:
    return db.get(ExtractionTemplate, template_id) if template_id else None

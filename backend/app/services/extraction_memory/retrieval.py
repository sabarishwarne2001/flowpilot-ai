"""ARCH41-S2:retrieval — which reviewed documents to show the model, decrypted."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.encryption import decrypt_secret
from app.models.extraction_memory import ExtractionExemplar
from app.services.extraction_memory import vocabulary as v

logger = logging.getLogger("app.services.extraction_memory.retrieval")


@dataclass
class ExemplarField:
    exemplar_id: uuid.UUID
    field_path: str
    value: str
    evidence: Optional[str]
    corrected: bool


@dataclass
class ExemplarDoc:
    work_item_id: uuid.UUID
    fields: list[ExemplarField] = field(default_factory=list)

    @property
    def has_correction(self) -> bool:
        return any(f.corrected for f in self.fields)


def _decrypt(row: ExtractionExemplar) -> Optional[ExemplarField]:
    try:
        value = decrypt_secret(row.value_ciphertext)
        evidence = decrypt_secret(row.evidence_ciphertext) if row.evidence_ciphertext else None
    except Exception:  # noqa: BLE001 - a rotated-away key loses one example, not the extraction
        logger.warning("extraction_memory.exemplar_undecryptable", extra={"exemplar_id": str(row.id)})
        return None
    return ExemplarField(row.id, row.field_path, value, evidence, row.label_source == v.LABEL_CORRECTED)


def _group(rows: Iterable[ExtractionExemplar]) -> list[ExemplarDoc]:
    docs: dict[uuid.UUID, ExemplarDoc] = {}
    for row in rows:
        decoded = _decrypt(row)
        if decoded is None:
            continue
        docs.setdefault(row.work_item_id, ExemplarDoc(row.work_item_id)).fields.append(decoded)
    return list(docs.values())


def exemplar_documents(
    db: Session,
    *,
    template_id: uuid.UUID,
    exclude_work_item_id: Optional[uuid.UUID],
    limit: int = v.MAX_EXEMPLAR_DOCS,
) -> list[ExemplarDoc]:
    """Newest reviewed documents of the layout, those with corrections first.

    A document that needed correcting teaches more than one that was right, so
    it is preferred; recency breaks ties because layouts drift.
    """
    statement = (
        select(ExtractionExemplar)
        .where(ExtractionExemplar.template_id == template_id)
        .order_by(ExtractionExemplar.created_at.desc())
        .limit(400)
    )
    if exclude_work_item_id is not None:
        statement = statement.where(ExtractionExemplar.work_item_id != exclude_work_item_id)
    docs = _group(db.execute(statement).scalars())
    docs.sort(key=lambda d: 0 if d.has_correction else 1)
    return docs[:limit]


def by_ids(db: Session, exemplar_ids: Sequence[uuid.UUID]) -> list[ExemplarDoc]:
    if not exemplar_ids:
        return []
    rows = db.execute(select(ExtractionExemplar).where(ExtractionExemplar.id.in_(list(exemplar_ids)))).scalars()
    return _group(rows)

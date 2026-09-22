"""HARDENING-T1:D18 — the missing writer of `document_roles`.

ARCH-31's plan was "OCR'd -> role classified -> line items normalised". The
classifier (`role_classifier.classify`) and the matcher were built; the step
between them was not, and nothing in the product ever wrote a
`document_roles` row. `procurement.score` sweeps only INVOICE rows, the role
override route needs an existing row, and the radar's contract-drift
detector joins on CONTRACT rows, so three-way matching and two radar
detectors never ran on a real upload.

`classify_and_store` is the function `role_classifier`'s docstring names.

WHAT IT WRITES
==============

role / role_source / role_confidence
    From `role_classifier.classify` over the document's extracted text. A row
    whose `role_source` is USER keeps its role: a person's correction is never
    overwritten (the override rule in role_classifier's header). Its header
    facts are still refreshed, because a reprocessed document has new ones.

vendor_key, document_number, document_date, currency, total_micros,
normalization_warnings
    From `line_extraction.extract_lines` with the workspace's currency and
    date format — the same call, with the same two settings, that
    `case_service` makes when it scores. Reading them any other way is how a
    workspace setting turns into a phantom variance.

The upsert is keyed on `uq_document_roles_work_item`, so a re-run (retry,
reprocess) updates the one row in place.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.document_role import DocumentRole
from app.services.procurement_matching import line_extraction
from app.services.procurement_matching.role_classifier import (
    SOURCE_CLASSIFIER,
    SOURCE_USER,
    classify,
)

logger = logging.getLogger("app.services.procurement_matching.role_service")

_MAX_WARNINGS = 20


@dataclass(frozen=True)
class RoleOutcome:
    work_item_id: uuid.UUID
    role: str
    role_source: str
    confidence: Optional[Decimal]
    user_locked: bool
    vendor_key: Optional[str]
    document_number: Optional[str]

    def as_details(self) -> dict[str, Any]:
        return {
            "work_item_id": str(self.work_item_id),
            "role": self.role,
            "role_source": self.role_source,
            "confidence": None if self.confidence is None else str(self.confidence),
            "user_locked": self.user_locked,
            "has_vendor_key": self.vendor_key is not None,
            "has_document_number": self.document_number is not None,
        }


def _workspace_settings(db: Session, workspace_id: uuid.UUID) -> tuple[Optional[str], Optional[str]]:
    from app.services.procurement_matching import case_service

    return case_service._workspace_settings(db, workspace_id)


def classify_and_store(
    db: Session,
    *,
    work_item: Any,
    organization_id: uuid.UUID,
) -> RoleOutcome:
    """Classify one extracted document and upsert its `document_roles` row.

    Flushes; the caller commits. Deterministic for a given text, entities and
    workspace settings.
    """
    existing = db.execute(
        select(DocumentRole.role, DocumentRole.role_source).where(
            DocumentRole.work_item_id == work_item.id
        )
    ).one_or_none()
    user_locked = existing is not None and existing.role_source == SOURCE_USER

    currency, date_format = _workspace_settings(db, work_item.workspace_id)
    lines = line_extraction.extract_lines(
        work_item.extracted_entities,
        workspace_currency=currency,
        date_format=date_format,
    )
    header = lines.header

    if user_locked:
        role, source, confidence = existing.role, SOURCE_USER, None
    else:
        verdict = classify(work_item.extracted_text or "")
        role, source, confidence = verdict.role, SOURCE_CLASSIFIER, verdict.confidence

    values = {
        "organization_id": organization_id,
        "workspace_id": work_item.workspace_id,
        "work_item_id": work_item.id,
        "role": role,
        "role_source": source,
        "role_confidence": confidence,
        "vendor_key": header.vendor_key,
        "document_number": header.document_number,
        "document_date": header.document_date,
        "currency": header.currency,
        "total_micros": header.total_micros,
        "normalization_warnings": list(header.warnings)[:_MAX_WARNINGS],
    }
    update_columns = {
        key: value
        for key, value in values.items()
        if key not in ("organization_id", "workspace_id", "work_item_id")
    }
    statement = (
        pg_insert(DocumentRole)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_document_roles_work_item",
            set_=update_columns,
        )
    )
    db.execute(statement)
    db.flush()

    outcome = RoleOutcome(
        work_item_id=work_item.id,
        role=role,
        role_source=source,
        confidence=confidence,
        user_locked=user_locked,
        vendor_key=header.vendor_key,
        document_number=header.document_number,
    )
    logger.info("procurement.role_stored", extra=outcome.as_details())
    return outcome


__all__ = ["RoleOutcome", "classify_and_store"]

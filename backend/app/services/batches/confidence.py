"""How sure the extraction is, per document and per field, read from verification.

Verification (ARCH-13) has independent agents re-extract a document and scores each field by their
agreement; its document-level `confidence` and per-field `confidence` are the numbers used here.
A document that was never verified has no score: it is "unscored", never a guessed number.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.verification import (
    BLOCKING_STATUSES,
    DocumentVerification,
    DocumentVerificationField,
)


@dataclass(frozen=True)
class FieldConfidence:
    field: str
    confidence: float
    agreed: bool
    disagreement: Optional[str]


@dataclass(frozen=True)
class DocumentConfidence:
    work_item_id: uuid.UUID
    confidence: Optional[float]
    verification_status: Optional[str]
    #: The document waits in the review queue (verification PENDING or DISAGREED).
    blocking: bool
    #: Why it waits, in the words verification recorded.
    reason: Optional[str]
    fields: tuple[FieldConfidence, ...] = field(default_factory=tuple)


def _reason(verification: DocumentVerification, disagreed: int) -> Optional[str]:
    if verification.status not in BLOCKING_STATUSES:
        return None
    if disagreed:
        return f"The verification agents disagree on {disagreed} field{'s' if disagreed != 1 else ''}."
    details = verification.details or {}
    calibration = details.get("calibration") if isinstance(details, dict) else None
    if isinstance(calibration, dict) and calibration.get("explanation"):
        return str(calibration["explanation"])
    if verification.status.value == "PENDING":
        return "Verification has not finished."
    return "It is waiting in the review queue."


def for_documents(db: Session, work_item_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, DocumentConfidence]:
    """The latest verification of each document, with its fields. Missing ids were never verified."""
    ids = list({i for i in work_item_ids})
    if not ids:
        return {}
    latest = (
        select(DocumentVerification)
        .where(DocumentVerification.work_item_id.in_(ids))
        .order_by(DocumentVerification.work_item_id, DocumentVerification.created_at.desc())
        .distinct(DocumentVerification.work_item_id)
    )
    verifications = list(db.execute(latest).scalars())
    by_verification: dict[uuid.UUID, list[FieldConfidence]] = {v.id: [] for v in verifications}
    if verifications:
        rows = db.execute(
            select(
                DocumentVerificationField.verification_id,
                DocumentVerificationField.field_path,
                DocumentVerificationField.confidence,
                DocumentVerificationField.agreed,
                DocumentVerificationField.disagreement_kind,
            ).where(DocumentVerificationField.verification_id.in_(list(by_verification)))
        ).all()
        for verification_id, path, conf, agreed, kind in rows:
            by_verification[verification_id].append(
                FieldConfidence(
                    field=path,
                    confidence=float(conf),
                    agreed=bool(agreed),
                    disagreement=kind.value if kind is not None else None,
                )
            )
    out: dict[uuid.UUID, DocumentConfidence] = {}
    for verification in verifications:
        fields = tuple(sorted(by_verification[verification.id], key=lambda f: f.field))
        disagreed = sum(1 for f in fields if not f.agreed)
        out[verification.work_item_id] = DocumentConfidence(
            work_item_id=verification.work_item_id,
            confidence=float(verification.confidence) if verification.confidence is not None else None,
            verification_status=verification.status.value,
            blocking=verification.status in BLOCKING_STATUSES,
            reason=_reason(verification, disagreed),
            fields=fields,
        )
    return out


__all__ = ["DocumentConfidence", "FieldConfidence", "for_documents"]

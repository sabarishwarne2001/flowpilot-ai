"""ARCH43-S1:requests — missing-document requests with single-use tokens.

The token is 32 random bytes shown ONCE to the person who creates the
request; the database keeps only its SHA-256 (ck_document_requests_token_hash
refuses anything else). consume() is one conditional UPDATE -- status OPEN
and not expired, set FULFILLED -- so two concurrent uploads cannot both use
it, and a used, expired or revoked token is refused before any file is read.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.cases import Case, CaseTemplate, DocumentRequest
from app.services.cases import vocabulary as v


class RequestError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create(db: Session, *, case: Case, document_type: str, recipient_label: str, actor_user_id: Optional[uuid.UUID],
           ttl_hours: Optional[int] = None) -> tuple[DocumentRequest, str]:
    if case.status == v.CASE_CLOSED:
        raise RequestError("CASE_CLOSED", "a closed case cannot request documents")
    template = db.get(CaseTemplate, case.template_id)
    token = secrets.token_urlsafe(32)
    hours = int(ttl_hours or template.request_ttl_hours)
    if not 1 <= hours <= 2160:
        raise RequestError("INVALID_TTL", "ttl_hours must be 1-2160")
    request = DocumentRequest(id=uuid.uuid4(), organization_id=case.organization_id, workspace_id=case.workspace_id,
                              case_id=case.id, document_type=document_type[:64], recipient_label=recipient_label[:200],
                              token_hash=digest(token), status=v.REQUEST_OPEN,
                              created_at=datetime.now(timezone.utc),
                              expires_at=datetime.now(timezone.utc) + timedelta(hours=hours), created_by_user_id=actor_user_id)
    db.add(request)
    db.flush()
    return request, token


def peek(db: Session, token: str) -> DocumentRequest:
    request = db.execute(select(DocumentRequest).where(DocumentRequest.token_hash == digest(token))).scalar_one_or_none()
    if request is None:
        raise RequestError("UNKNOWN", "This link is not valid.")
    if request.status == v.REQUEST_OPEN and request.expires_at <= datetime.now(timezone.utc):
        raise RequestError("EXPIRED", "This link has expired.")
    if request.status != v.REQUEST_OPEN:
        raise RequestError(request.status, "This link has already been used or withdrawn.")
    return request


def consume(db: Session, token: str) -> DocumentRequest:
    row = db.execute(
        update(DocumentRequest)
        .where(DocumentRequest.token_hash == digest(token), DocumentRequest.status == v.REQUEST_OPEN,
               DocumentRequest.expires_at > datetime.now(timezone.utc))
        .values(status=v.REQUEST_FULFILLED, used_at=datetime.now(timezone.utc))
        .returning(DocumentRequest.id)
        .execution_options(synchronize_session=False)
    ).first()
    if row is None:
        raise RequestError("USED", "This link has already been used, has expired or was withdrawn.")
    request = db.get(DocumentRequest, row[0])
    db.refresh(request)
    return request


def revoke(db: Session, *, request: DocumentRequest) -> DocumentRequest:
    if request.status != v.REQUEST_OPEN:
        raise RequestError("NOT_OPEN", "only an open request can be withdrawn")
    request.status = v.REQUEST_REVOKED
    db.flush()
    return request


def expire(db: Session) -> int:
    result = db.execute(update(DocumentRequest).where(DocumentRequest.status == v.REQUEST_OPEN,
                                                      DocumentRequest.expires_at <= datetime.now(timezone.utc))
                        .values(status=v.REQUEST_EXPIRED).execution_options(synchronize_session=False))
    return int(result.rowcount or 0)


def fulfil_upload(db: Session, *, token: str, handle: Any, size: int, filename: str, declared_mime: Optional[str]) -> dict[str, Any]:
    """Validate the file, consume the token, ingest into the case's workspace, add to the case, re-evaluate.
    One transaction: if intake fails, the token is still unused."""
    from app.core.config import settings
    from app.models.work_item import WorkItem
    from app.services import document_intake_service, file_validation_service
    from app.services.cases import assembly

    pending = peek(db, token)
    validated = file_validation_service.validate_spooled(
        handle, size, declared_mime=declared_mime, original_filename=filename[:255],
        allowed_mimes=file_validation_service.workspace_allowed_mimes(db, pending.workspace_id, settings.ALLOWED_MIME_TYPES),
        max_pages=settings.MAX_DOCUMENT_PAGES, scrub_metadata=settings.SCRUB_UPLOAD_METADATA)
    request = consume(db, token)
    with validated:
        result = document_intake_service.ingest_validated(
            db, validated, organization_id=request.organization_id, workspace_id=request.workspace_id,
            uploader_id=None, enqueue_extraction=True)
    item: WorkItem = result.work_item
    request.fulfilled_work_item_id = item.id
    case = db.get(Case, request.case_id)
    assembly.add_document(db, case=case, work_item_id=item.id, document_type=request.document_type, source=v.SOURCE_REQUEST)
    assembly.evaluate(db, case=case)
    db.flush()
    return {"received": True, "document_type": request.document_type, "work_item_id": str(item.id)}


__all__ = ["RequestError", "consume", "create", "digest", "expire", "fulfil_upload", "peek", "revoke"]

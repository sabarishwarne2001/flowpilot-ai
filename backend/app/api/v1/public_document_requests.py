"""ARCH43-S1:api-public-requests — where the recipient of a missing-document
request uploads it. No account: the single-use token in the path is the only
credential (registered in app/core/public_route_registry.PUBLIC_ROUTES with
the public rate-limit policy). Nothing about the case beyond its title and
the document type asked for is ever returned."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.exceptions import SpendLimitExceededError
from app.models.cases import Case
from app.schemas.cases import PublicRequestInfo, PublicUploadResult
from app.services import file_validation_service, plan_admission
from app.services.cases import requests

logger = logging.getLogger("app.api.v1.public_document_requests")

router = APIRouter(tags=["Document Requests (public)"])

_GONE = {"USED", "FULFILLED", "EXPIRED", "REVOKED"}


def _refuse(exc: requests.RequestError) -> HTTPException:
    return HTTPException(status_code=410 if exc.code in _GONE else 404, detail={"code": exc.code, "message": str(exc)})


@router.get("/public/document-requests/{token}", response_model=PublicRequestInfo)
def preview_document_request(token: str, db: Session = Depends(get_db)) -> PublicRequestInfo:
    try:
        request = requests.peek(db, token)
    except requests.RequestError as exc:
        raise _refuse(exc) from exc
    case = db.get(Case, request.case_id)
    return PublicRequestInfo(document_type=request.document_type, case_title=case.title if case else "",
                             expires_at=request.expires_at)


def _rejected(exc: file_validation_service.FileValidationError) -> HTTPException:
    if exc.reason is file_validation_service.RejectionReason.TOO_LARGE:
        return HTTPException(status_code=413, detail={"code": "FILE_TOO_LARGE", "message": str(exc)})
    return HTTPException(status_code=422, detail={"code": "FILE_REJECTED", "message": str(exc)})


# F-218. A plain `def`, so the spool, validation, storage write and commit run in
# the threadpool rather than on the event loop.
@router.post("/public/document-requests/{token}", response_model=PublicUploadResult)
def upload_requested_document(token: str, file: UploadFile = File(...), db: Session = Depends(get_db)) -> PublicUploadResult:
    try:
        pending = requests.peek(db, token)
    except requests.RequestError as exc:
        raise _refuse(exc) from exc
    # F-220. The spooler's own refusals (empty, over the limit) were raised outside
    # any handler: a 500 to the recipient. The link stays open for a corrected file.
    # Campaign session 1: no more than the requesting organization's plan accepts.
    max_bytes = plan_admission.max_upload_bytes(db, organization_id=pending.organization_id)
    try:
        handle, size = file_validation_service.spool_upload_file(file, max_bytes=max_bytes)
    except file_validation_service.FileValidationError as exc:
        raise _rejected(exc) from exc
    try:
        result = requests.fulfil_upload(db, token=token, handle=handle, size=size, filename=file.filename or "document",
                                        declared_mime=file.content_type)
    except requests.RequestError as exc:
        db.rollback()
        raise _refuse(exc) from exc
    except file_validation_service.FileValidationError as exc:
        db.rollback()
        raise _rejected(exc) from exc
    except (SpendLimitExceededError, plan_admission.PlanLimitExceededError) as exc:
        # Campaign session 1. The upload is charged like any other, so the
        # organization's allowance applies. The person here has no account and no
        # say over the plan: they are told what they can act on, nothing about the
        # plan, and the link stays open (the token was not consumed).
        db.rollback()
        logger.info(
            "public_request.refused_by_allowance",
            extra={"organization_id": str(pending.organization_id), "reason": type(exc).__name__},
        )
        raise HTTPException(
            status_code=402,
            detail={
                "code": "RECIPIENT_CANNOT_ACCEPT",
                "message": (
                    "This organization cannot accept this document right now. Please contact the "
                    "person who sent you this link; it stays open for a later upload."
                ),
            },
        ) from exc
    db.commit()
    return PublicUploadResult(received=True, document_type=result["document_type"])


__all__ = ["router"]

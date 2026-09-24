"""ARCH43-S1:api-public-requests — where the recipient of a missing-document
request uploads it. No account: the single-use token in the path is the only
credential (registered in app/core/public_route_registry.PUBLIC_ROUTES with
the public rate-limit policy). Nothing about the case beyond its title and
the document type asked for is ever returned."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import settings
from app.models.cases import Case
from app.schemas.cases import PublicRequestInfo, PublicUploadResult
from app.services import file_validation_service
from app.services.cases import requests

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


@router.post("/public/document-requests/{token}", response_model=PublicUploadResult)
async def upload_requested_document(token: str, file: UploadFile = File(...), db: Session = Depends(get_db)) -> PublicUploadResult:
    try:
        requests.peek(db, token)
    except requests.RequestError as exc:
        raise _refuse(exc) from exc
    handle, size = await file_validation_service.spool_upload_file(file, max_bytes=settings.MAX_UPLOAD_SIZE)
    try:
        result = requests.fulfil_upload(db, token=token, handle=handle, size=size, filename=file.filename or "document",
                                        declared_mime=file.content_type)
    except requests.RequestError as exc:
        db.rollback()
        raise _refuse(exc) from exc
    except file_validation_service.FileValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail={"code": "FILE_REJECTED", "message": str(exc)}) from exc
    db.commit()
    return PublicUploadResult(received=True, document_type=result["document_type"])


__all__ = ["router"]

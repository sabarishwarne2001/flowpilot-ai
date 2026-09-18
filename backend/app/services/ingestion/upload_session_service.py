"""ARCH-38 — resumable upload sessions over the storage driver's multipart API.

THE CONTRACT
============

1. `create(...)` begins a multipart upload on the driver and writes an
   `upload_sessions` row holding the object key, the backend's upload id, the
   part size and the expected sha256.
2. The client `PUT`s parts. `receive_part(...)` is idempotent by part number:
   re-sending part 3 overwrites part 3 and does not append to
   `parts_received` twice. That is what makes resume after a disconnect safe.
3. `complete(...)` assembles the object, recomputes the sha256 over the
   assembled bytes, and refuses on a mismatch -- aborting the upload and
   deleting anything that landed, so a corrupted object is never a document.
4. `resume(...)` returns the parts the server already holds. The browser asks
   the server what it has rather than trusting what it remembers, which is why
   a page reload loses nothing.

WHY `parts_received` IS LOCKED
==============================

The browser uploads three files at a time, and within a file may retry a part
while another is in flight. `array_append` in a read-modify-write without a row
lock is a lost update: two concurrent parts read `{1}`, both write `{1,n}`, and
one part is forgotten -- which surfaces later as a completion that hangs
waiting for a part the server was told about and did not record.
`_locked_session` takes `FOR UPDATE` on the row for exactly that reason.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import (
    DEFAULT_PART_SIZE,
    ObjectNotFoundError,
    StorageError,
    StorageNamespace,
    UploadedPart,
    get_storage_driver,
    tenant_key,
)
from app.models.ingestion import UploadSession

logger = logging.getLogger("app.services.ingestion.upload_session")

#: How long a half-finished upload is kept before the sweeper abandons it.
SESSION_TTL_HOURS = 24

#: Suffix chosen from the declared MIME, mirroring document_intake_service.
_SUFFIX_BY_MIME: dict[str, str] = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/tiff": "tiff",
    "image/webp": "webp",
    "image/bmp": "bmp",
}


class UploadSessionError(RuntimeError):
    """A session was used in a way its state does not allow."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _locked_session(
    db: Session, *, workspace_id: uuid.UUID, session_id: uuid.UUID
) -> UploadSession:
    """Load a session for update, scoped to its workspace.

    The workspace predicate is in the SQL, not applied afterwards: a session id
    from another tenant must be indistinguishable from one that does not exist.
    """
    row = db.execute(
        select(UploadSession)
        .where(
            UploadSession.id == session_id,
            UploadSession.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise UploadSessionError("SESSION_NOT_FOUND", "Upload session not found.")
    return row


def create(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    filename: str,
    mime_type: Optional[str],
    total_size: Optional[int],
    expected_sha256: Optional[str],
    part_size: int = DEFAULT_PART_SIZE,
) -> UploadSession:
    driver = get_storage_driver()
    file_id = uuid.uuid4()
    key = tenant_key(
        organization_id=organization_id,
        namespace=StorageNamespace.DOCUMENTS,
        file_id=file_id,
        suffix=_SUFFIX_BY_MIME.get((mime_type or "").lower()),
    )

    begun = driver.create_multipart(key, mime_type or "application/octet-stream", part_size=part_size)

    session = UploadSession(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        organization_id=organization_id,
        created_by_user_id=user_id,
        object_key=begun.key,
        minio_upload_id=begun.upload_id,
        part_size=begun.part_size,
        total_size=total_size,
        filename=filename[:255],
        mime_type=(mime_type or None),
        parts_received=[],
        expected_sha256=(expected_sha256.lower() if expected_sha256 else None),
        expires_at=_now() + timedelta(hours=SESSION_TTL_HOURS),
        status="ACTIVE",
    )
    db.add(session)
    db.flush([session])
    return session


def receive_part(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    part_number: int,
    data: bytes,
) -> UploadSession:
    session = _locked_session(db, workspace_id=workspace_id, session_id=session_id)

    if session.status != "ACTIVE":
        raise UploadSessionError(
            "SESSION_NOT_ACTIVE",
            f"This upload session is {session.status.lower()}.",
        )
    if session.expires_at <= _now():
        session.status = "EXPIRED"
        raise UploadSessionError("SESSION_EXPIRED", "This upload session has expired.")
    if not data:
        raise UploadSessionError("PART_EMPTY", "A part must carry at least one byte.")

    driver = get_storage_driver()
    try:
        driver.upload_part(
            session.object_key,
            str(session.minio_upload_id),
            part_number,
            data,
        )
    except StorageError as exc:
        raise UploadSessionError("PART_REJECTED", str(exc)) from exc

    # Idempotent by part number. Re-sending a part replaces it on the backend
    # and leaves this list unchanged, so the count is parts held, not parts
    # sent.
    if part_number not in session.parts_received:
        session.parts_received = sorted([*session.parts_received, part_number])
    db.flush([session])
    return session


def resume(
    db: Session, *, workspace_id: uuid.UUID, session_id: uuid.UUID
) -> UploadSession:
    """What the server holds. The browser's own record is never consulted."""
    row = db.execute(
        select(UploadSession).where(
            UploadSession.id == session_id,
            UploadSession.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise UploadSessionError("SESSION_NOT_FOUND", "Upload session not found.")
    return row


def abort(
    db: Session, *, workspace_id: uuid.UUID, session_id: uuid.UUID
) -> UploadSession:
    session = _locked_session(db, workspace_id=workspace_id, session_id=session_id)
    if session.status == "ACTIVE":
        try:
            get_storage_driver().abort_multipart(
                session.object_key, str(session.minio_upload_id)
            )
        except StorageError:
            logger.warning(
                "upload_session.abort_failed",
                extra={"session_id": str(session.id)},
            )
        session.status = "ABORTED"
        db.flush([session])
    return session


def complete(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
) -> tuple[UploadSession, bytes]:
    """Assemble the object and verify its sha256.

    Returns the session and the assembled bytes, so the caller can hand them to
    `document_intake_service.ingest_validated` through the ordinary validation
    pipeline. The bytes go back through validation deliberately: a resumable
    upload must not be a way to put an unvalidated file into a workspace.
    """
    session = _locked_session(db, workspace_id=workspace_id, session_id=session_id)

    if session.status != "ACTIVE":
        raise UploadSessionError(
            "SESSION_NOT_ACTIVE", f"This upload session is {session.status.lower()}."
        )
    if not session.parts_received:
        raise UploadSessionError("NO_PARTS", "No parts have been uploaded.")

    expected_numbers = list(range(1, len(session.parts_received) + 1))
    if sorted(session.parts_received) != expected_numbers:
        missing = sorted(set(expected_numbers) - set(session.parts_received))
        raise UploadSessionError(
            "PARTS_MISSING",
            f"Parts {missing} have not been uploaded.",
        )

    driver = get_storage_driver()
    parts = [
        UploadedPart(part_number=number, etag="", size=0)
        for number in sorted(session.parts_received)
    ]
    try:
        stored = driver.complete_multipart(
            session.object_key,
            str(session.minio_upload_id),
            parts,
            session.mime_type or "application/octet-stream",
        )
    except StorageError as exc:
        raise UploadSessionError("ASSEMBLY_FAILED", str(exc)) from exc

    try:
        payload = driver.get(stored.key)
    except ObjectNotFoundError as exc:
        raise UploadSessionError(
            "ASSEMBLY_FAILED", "The assembled object could not be read back."
        ) from exc

    actual = hashlib.sha256(payload).hexdigest()
    if session.expected_sha256 and actual != session.expected_sha256:
        # The object is wrong. Remove it before anything can reference it: a
        # failed integrity check that leaves the bytes in the bucket is a
        # failed integrity check that somebody will later "recover" from.
        try:
            driver.delete(stored.key)
        except StorageError:
            logger.exception(
                "upload_session.orphan_after_sha_mismatch",
                extra={"key": stored.key},
            )
        session.status = "ABORTED"
        db.flush([session])
        raise UploadSessionError(
            "SHA256_MISMATCH",
            "The assembled file does not match the checksum the client "
            f"declared (expected {session.expected_sha256}, got {actual}).",
        )

    session.status = "COMPLETED"
    session.completed_at = _now()
    session.total_size = len(payload)
    db.flush([session])
    return session, payload


def sweep_expired(db: Session, *, limit: int = 200) -> int:
    """Abort sessions past their TTL. Called by the batch sweeper."""
    rows = (
        db.execute(
            select(UploadSession)
            .where(
                UploadSession.status == "ACTIVE",
                UploadSession.expires_at <= _now(),
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )
    driver = get_storage_driver()
    for row in rows:
        try:
            driver.abort_multipart(row.object_key, str(row.minio_upload_id))
        except StorageError:
            logger.warning(
                "upload_session.sweep_abort_failed", extra={"session_id": str(row.id)}
            )
        row.status = "EXPIRED"
    if rows:
        db.flush()
    return len(rows)


__all__ = [
    "SESSION_TTL_HOURS",
    "UploadSessionError",
    "abort",
    "complete",
    "create",
    "receive_part",
    "resume",
    "sweep_expired",
]

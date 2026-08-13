"""User avatar upload, validation and storage (ARCH-06 Step 7, ARCH-07 Step 5).

ARCH-07 Step 5: every filesystem call is replaced by StorageDriver.
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from datetime import UTC, datetime
from typing import Optional

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.storage import ObjectNotFoundError, get_storage_driver
from app.core.storage.keys import legacy_path_to_key
from app.models.uploaded_file import UploadedFile
from app.models.user import User

logger = logging.getLogger("app.services.avatar")

MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_DIMENSION = 1024
OUTPUT_FORMAT = "PNG"
OUTPUT_MIME = "image/png"
OUTPUT_EXTENSION = "png"
MAX_AVATAR_BYTES_PER_USER = 10 * 1024 * 1024


class AvatarError(Exception):
    """Base class for avatar workflow failures."""


class InvalidImageError(AvatarError, ValueError):
    """The uploaded bytes are not an acceptable avatar."""


class ImageTooLargeError(AvatarError, ValueError):
    """Avatar file or dimension bounds exceeded."""


class QuotaExceededError(AvatarError):
    """Storage quota exceeded."""


class AvatarNotFoundError(AvatarError):
    """Avatar record or file not found."""


def _avatar_key(user_id: uuid.UUID) -> str:
    return f"avatars/{user_id}/{uuid.uuid4().hex}.{OUTPUT_EXTENSION}"


def _validate_and_normalise(raw: bytes) -> bytes:
    if not raw:
        raise InvalidImageError("Empty upload.")
    if len(raw) > MAX_AVATAR_BYTES:
        raise ImageTooLargeError(
            f"Avatar exceeds {MAX_AVATAR_BYTES // (1024 * 1024)} MB."
        )

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError("File is not a valid image.") from exc

    try:
        with Image.open(io.BytesIO(raw)) as image:
            if max(image.size) > MAX_DIMENSION:
                raise ImageTooLargeError(
                    f"Avatar dimensions exceed {MAX_DIMENSION}px."
                )
            converted = image.convert("RGBA")
            buffer = io.BytesIO()
            converted.save(buffer, format=OUTPUT_FORMAT, optimize=True)
            return buffer.getvalue()
    except (InvalidImageError, ImageTooLargeError):
        raise
    except (OSError, ValueError) as exc:
        raise InvalidImageError("Image could not be processed.") from exc


def _live_bytes_for_user(db: Session, *, owner_id: uuid.UUID) -> int:
    return db.execute(
        select(func.coalesce(func.sum(UploadedFile.file_size), 0)).where(
            UploadedFile.owner_id == owner_id,
            UploadedFile.deleted_at.is_(None),
        )
    ).scalar_one()


def set_avatar(
    db: Session,
    *,
    owner: User,
    raw: bytes,
    original_filename: str = "avatar.png",
) -> UploadedFile:
    normalised = _validate_and_normalise(raw)

    if (
        _live_bytes_for_user(db, owner_id=owner.id) + len(normalised)
        > MAX_AVATAR_BYTES_PER_USER
    ):
        raise QuotaExceededError(
            "You have reached your upload storage limit. Remove an existing "
            "file and try again."
        )

    driver = get_storage_driver()
    key = _avatar_key(owner.id)

    driver.put(key, normalised, OUTPUT_MIME)

    previous = None
    if owner.avatar_file_id is not None:
        previous = db.get(UploadedFile, owner.avatar_file_id)

    if previous is not None:
        previous.deleted_at = datetime.now(UTC)

    record = UploadedFile(
        owner_id=owner.id,
        organization_id=None,
        workspace_id=None,
        file_path=key,
        original_filename=(original_filename or "avatar.png")[:255],
        mime_type=OUTPUT_MIME,
        file_size=len(normalised),
        checksum_sha256=hashlib.sha256(normalised).hexdigest(),
    )
    db.add(record)
    db.flush()

    owner.avatar_file_id = record.id
    db.add(owner)
    db.commit()
    db.refresh(record)

    logger.info(
        "AUDIT | AVATAR_SET | user=%s | file=%s | bytes=%d | mime=%s",
        owner.id, record.id, len(normalised), OUTPUT_MIME,
    )
    return record


def clear_avatar(db: Session, *, owner: User) -> None:
    uploaded = resolve_current(db, owner=owner)
    uploaded.deleted_at = datetime.now(UTC)
    owner.avatar_file_id = None
    db.add_all([uploaded, owner])
    db.commit()

    logger.info("AUDIT | AVATAR_CLEARED | user=%s | file=%s", owner.id, uploaded.id)


def resolve_current(db: Session, *, owner: User) -> UploadedFile:
    if owner.avatar_file_id is None:
        raise AvatarNotFoundError("Avatar not found.")

    uploaded = db.get(UploadedFile, owner.avatar_file_id)
    if uploaded is None or uploaded.deleted_at is not None or uploaded.owner_id != owner.id:
        raise AvatarNotFoundError("Avatar not found.")

    return uploaded


def read_avatar_bytes(db: Session, *, record: UploadedFile) -> bytes:
    driver = get_storage_driver()
    try:
        return driver.get(legacy_path_to_key(record.file_path))
    except ObjectNotFoundError:
        logger.error(
            "ARCH07_MISSING_OBJECT | uploaded_files.id=%s file_path=%s",
            record.id, record.file_path,
        )
        raise AvatarNotFoundError("Avatar file missing.")


def open_avatar_stream(record: UploadedFile):
    driver = get_storage_driver()
    try:
        return driver.stream(legacy_path_to_key(record.file_path))
    except ObjectNotFoundError:
        raise AvatarNotFoundError("Avatar file missing.")
"""
Avatar upload, validation, and storage for FlowPilot AI.

ARCH-06 Step 7. Closes A.2.2 (uploads validated by a client-supplied header)
and the per-user half of A.2.3 (no ownership record, no cleanup, no quota).

WHAT A.2.2 ACTUALLY SAID, AND WHAT CLOSING IT REQUIRES
----------------------------------------------------------
    ALLOWED_TYPES = {"image/png": ".png", ...}
    if file.content_type not in ALLOWED_TYPES:   # <- the Content-Type the CLIENT sent

`file.content_type` is a string the uploader chose. Nothing read the bytes.
The audit confirmed it: `grep -rn "PIL|Pillow|imghdr|magic"` returned
nothing.

Header checking is not made safe by adding more header checking. This module
decodes the bytes and rejects anything the decoder will not accept as the
image it claims to be — `Image.open()` then `verify()`, then a second open
for the re-encode, because `verify()` leaves the file object unusable and
Pillow's own documentation requires reopening after it.

`_validate_and_normalize` returns re-encoded bytes, not the original. That is
deliberate and does three things at once:

  1. It strips EXIF, which routinely carries GPS coordinates. A user
     uploading a phone photo as an avatar should not thereby publish where
     they live.
  2. It guarantees the stored bytes are what Pillow produced from a
     successful decode, so a payload that survives validation by exploiting
     a parser quirk does not survive being written back out.
  3. It makes the stored MIME type a fact about the file rather than a claim
     about it, which is what §B.7's streaming route needs in order to set
     Content-Type safely.

A polyglot file — valid PNG header, HTML payload appended — is the case that
motivates (2). It passes `verify()`. It does not survive re-encoding,
because the re-encode writes only the decoded pixel data.

DECOMPRESSION BOMBS
-------------------
`MAX_PIXELS` is checked BEFORE the re-encode. A 64000x64000 PNG can be a few
hundred KB on disk and 12 GB decoded; `MAX_FILE_SIZE` alone does not bound
memory, and Pillow's own `DecompressionBombWarning` is a warning, not a
refusal. The dimension check is what makes the size limit meaningful.

WHAT THIS MODULE DOES NOT DO
-------------------------------
No moderation. The User model's §B.4 note lists "a moderation question"
alongside validation and lifecycle as reasons avatars were deferred;
validation and lifecycle are closed here, moderation is not, and pretending
otherwise would be worse than leaving it named. An avatar is visible to
other members of the same tenant, so this is a real gap — it belongs to
ARCH-07 with the rest of the abuse surface.
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.uploaded_file import UploadedFile
from app.models.user import User

logger = logging.getLogger("app.services.avatar")


AVATAR_DIR = Path("uploads/avatars")
AVATAR_DIR.mkdir(parents=True, exist_ok=True)

_AVATAR_ROOT = AVATAR_DIR.resolve()

ACCEPTED_FORMATS: dict[str, tuple[str, str]] = {
    "PNG": (".png", "image/png"),
    "JPEG": (".jpg", "image/jpeg"),
    "WEBP": (".webp", "image/webp"),
}

MAX_FILE_SIZE = 2 * 1024 * 1024
MAX_PIXELS = 8000 * 8000
MIN_DIMENSION = 16
MAX_DIMENSION = 4096
MAX_AVATAR_BYTES_PER_USER = 10 * 1024 * 1024


class AvatarError(Exception):
    """Base class for avatar workflow failures."""


class InvalidImageError(AvatarError):
    """The bytes are not a decodable image of an accepted format."""


class ImageTooLargeError(AvatarError):
    """The file or its decoded dimensions exceed the permitted bounds."""


class QuotaExceededError(AvatarError):
    """This user is already at their storage ceiling."""


class AvatarNotFoundError(AvatarError):
    """No live avatar for this user, or not one this caller may read."""


# ===========================================================================
# Validation
# ===========================================================================

def _validate_and_normalize(raw: bytes) -> tuple[bytes, str, str, int, int]:
    """
    Proves the bytes are a real image and returns a clean re-encode.
    """
    if not raw:
        raise InvalidImageError("The uploaded file is empty.")

    if len(raw) > MAX_FILE_SIZE:
        raise ImageTooLargeError(
            f"Avatars must be smaller than {MAX_FILE_SIZE // (1024 * 1024)} MB."
        )

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            width, height = probe.size
            probe.verify()
    except UnidentifiedImageError:
        raise InvalidImageError(
            "That file is not a valid PNG, JPEG, or WebP image."
        )
    except Image.DecompressionBombError:
        raise ImageTooLargeError("That image's dimensions are too large.")
    except Exception:
        raise InvalidImageError(
            "That file could not be read as an image."
        )

    if fmt not in ACCEPTED_FORMATS:
        raise InvalidImageError(
            f"{fmt or 'That format'} is not supported. Use PNG, JPEG, or WebP."
        )

    if width * height > MAX_PIXELS:
        raise ImageTooLargeError("That image's dimensions are too large.")
    if width < MIN_DIMENSION or height < MIN_DIMENSION:
        raise InvalidImageError(
            f"Avatars must be at least {MIN_DIMENSION}x{MIN_DIMENSION} pixels."
        )
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ImageTooLargeError(
            f"Avatars must be at most {MAX_DIMENSION}x{MAX_DIMENSION} pixels."
        )

    extension, mime_type = ACCEPTED_FORMATS[fmt]

    try:
        with Image.open(io.BytesIO(raw)) as image:
            if fmt == "JPEG":
                clean = image.convert("RGB")
            elif image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                clean = image.convert("RGBA")
            else:
                clean = image.copy()

            buffer = io.BytesIO()
            clean.save(buffer, format=fmt, optimize=True)
            normalized = buffer.getvalue()
    except Exception:
        raise InvalidImageError("That image could not be processed.")

    if not normalized:
        raise InvalidImageError("That image could not be processed.")

    return normalized, extension, mime_type, width, height


# ===========================================================================
# Quota & Path Resolution
# ===========================================================================

def _live_bytes_for_user(db: Session, *, owner_id) -> int:
    return db.execute(
        select(func.coalesce(func.sum(UploadedFile.file_size), 0)).where(
            UploadedFile.owner_id == owner_id,
            UploadedFile.deleted_at.is_(None),
        )
    ).scalar_one()


def resolve_stored_path(uploaded: UploadedFile) -> Path:
    candidate = (AVATAR_DIR / Path(uploaded.file_path).name).resolve()
    if candidate.parent != _AVATAR_ROOT:
        logger.error(
            "AVATAR_PATH_ESCAPE | file=%s | path=%s", uploaded.id, candidate
        )
        raise AvatarNotFoundError("Avatar not found.")
    return candidate


def resolve_current(db: Session, *, owner: User) -> UploadedFile:
    if owner.avatar_file_id is None:
        raise AvatarNotFoundError("Avatar not found.")

    uploaded = db.get(UploadedFile, owner.avatar_file_id)
    if uploaded is None or uploaded.deleted_at is not None:
        raise AvatarNotFoundError("Avatar not found.")

    if uploaded.owner_id != owner.id:
        logger.error(
            "AVATAR_OWNER_MISMATCH | user=%s | file=%s", owner.id, uploaded.id
        )
        raise AvatarNotFoundError("Avatar not found.")

    return uploaded


# ===========================================================================
# Write & Clear
# ===========================================================================

def set_avatar(
    db: Session,
    *,
    owner: User,
    raw: bytes,
    original_filename: str,
) -> UploadedFile:
    normalized, extension, mime_type, width, height = _validate_and_normalize(raw)

    if (
        _live_bytes_for_user(db, owner_id=owner.id) + len(normalized)
        > MAX_AVATAR_BYTES_PER_USER
    ):
        raise QuotaExceededError(
            "You have reached your upload storage limit. Remove an existing "
            "file and try again."
        )

    previous = None
    if owner.avatar_file_id is not None:
        previous = db.get(UploadedFile, owner.avatar_file_id)

    filename = f"{uuid.uuid4()}{extension}"
    destination = AVATAR_DIR / filename
    destination.write_bytes(normalized)

    uploaded = UploadedFile(
        owner_id=owner.id,
        organization_id=None,
        workspace_id=None,
        file_path=f"avatars/{filename}",
        original_filename=(original_filename or filename)[:255],
        mime_type=mime_type,
        file_size=len(normalized),
        checksum_sha256=hashlib.sha256(normalized).hexdigest(),
    )
    db.add(uploaded)
    db.flush()

    owner.avatar_file_id = uploaded.id
    db.add(owner)

    old_path = None
    if previous is not None and previous.id != uploaded.id:
        previous.deleted_at = datetime.now(UTC)
        db.add(previous)
        try:
            old_path = resolve_stored_path(previous)
        except AvatarNotFoundError:
            old_path = None

    db.commit()
    db.refresh(uploaded)

    if old_path is not None:
        try:
            old_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error(
                "AVATAR_UNLINK_FAILED | user=%s | path=%s | error=%s",
                owner.id, old_path, exc,
            )
        else:
            logger.info(
                "AUDIT | AVATAR_REPLACED | user=%s | removed=%s",
                owner.id, old_path.name,
            )

    logger.info(
        "AUDIT | AVATAR_SET | user=%s | file=%s | bytes=%d | %dx%d | mime=%s",
        owner.id, uploaded.id, len(normalized), width, height, mime_type,
    )
    return uploaded


def clear_avatar(db: Session, *, owner: User) -> None:
    uploaded = resolve_current(db, owner=owner)
    path = resolve_stored_path(uploaded)

    uploaded.deleted_at = datetime.now(UTC)
    owner.avatar_file_id = None
    db.add_all([uploaded, owner])
    db.commit()

    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.error(
            "AVATAR_UNLINK_FAILED | user=%s | path=%s | error=%s",
            owner.id, path, exc,
        )

    logger.info("AUDIT | AVATAR_CLEARED | user=%s | file=%s", owner.id, uploaded.id)
"""ARCH-10 Step 5 — validation before the file is durable."""

from __future__ import annotations

import hashlib
import io
import logging
import tempfile
from dataclasses import dataclass, field
from enum import Enum as PyEnum
from typing import Any, BinaryIO, Iterable, Optional

logger = logging.getLogger("app.services.file_validation")

SPOOL_MAX_MEMORY_BYTES = 4 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
SNIFF_BYTES = 4096
MAX_IMAGE_PIXELS = 180_000_000


class RejectionReason(str, PyEnum):
    EMPTY = "EMPTY"
    TOO_LARGE = "TOO_LARGE"
    UNKNOWN_SIGNATURE = "UNKNOWN_SIGNATURE"
    MIME_MISMATCH = "MIME_MISMATCH"
    MIME_NOT_ALLOWED = "MIME_NOT_ALLOWED"
    CORRUPT = "CORRUPT"
    TOO_MANY_PAGES = "TOO_MANY_PAGES"
    ENCRYPTED = "ENCRYPTED"
    ACTIVE_CONTENT = "ACTIVE_CONTENT"
    DECOMPRESSION_BOMB = "DECOMPRESSION_BOMB"


QUARANTINE_REASONS: frozenset[RejectionReason] = frozenset(
    {
        RejectionReason.MIME_MISMATCH,
        RejectionReason.ACTIVE_CONTENT,
        RejectionReason.UNKNOWN_SIGNATURE,
        RejectionReason.DECOMPRESSION_BOMB,
    }
)


class FileValidationError(Exception):
    """A file was refused before it became durable."""

    def __init__(
        self,
        reason: RejectionReason,
        message: str,
        *,
        detected_mime: Optional[str] = None,
        declared_mime: Optional[str] = None,
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.detected_mime = detected_mime
        self.declared_mime = declared_mime
        self.detail = detail or {}

    @property
    def should_quarantine(self) -> bool:
        return self.reason in QUARANTINE_REASONS

    def audit_details(self) -> dict[str, Any]:
        return {
            "reason": self.reason.value,
            "declared_mime": self.declared_mime,
            "detected_mime": self.detected_mime,
            "message": str(self),
            **self.detail,
        }


@dataclass
class ValidatedUpload:
    """A file that has passed every check and is ready to be made durable."""

    handle: BinaryIO
    size: int
    checksum_sha256: str
    mime_type: str
    original_filename: str
    page_count: Optional[int] = None
    scrubbed: bool = False
    notes: list[str] = field(default_factory=list)

    def close(self) -> None:
        try:
            self.handle.close()
        except Exception:
            pass

    def __enter__(self) -> "ValidatedUpload":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


@dataclass(frozen=True)
class Signature:
    mime: str
    magic: bytes
    offset: int = 0
    trailer: Optional[tuple[int, bytes]] = None


_SIGNATURES: tuple[Signature, ...] = (
    Signature("application/pdf", b"%PDF-"),
    Signature("image/png", b"\x89PNG\r\n\x1a\n"),
    Signature("image/jpeg", b"\xff\xd8\xff"),
    Signature("image/gif", b"GIF87a"),
    Signature("image/gif", b"GIF89a"),
    Signature("image/tiff", b"II*\x00"),
    Signature("image/tiff", b"MM\x00*"),
    Signature("image/webp", b"RIFF", trailer=(8, b"WEBP")),
    Signature("image/bmp", b"BM"),
    Signature("application/zip", b"PK\x03\x04"),
)

_ZIP_SUBTYPES: tuple[tuple[bytes, str], ...] = (
    (
        b"word/",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    (
        b"xl/",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    (
        b"ppt/",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
)

_MIME_ALIASES: dict[str, frozenset[str]] = {
    "image/jpeg": frozenset({"image/jpeg", "image/jpg", "image/pjpeg"}),
    "image/tiff": frozenset({"image/tiff", "image/x-tiff"}),
}

_IMAGE_MIMES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/tiff", "image/webp", "image/bmp"}
)


def sniff_mime(head: bytes) -> Optional[str]:
    if not head:
        return None

    for signature in _SIGNATURES:
        end = signature.offset + len(signature.magic)
        if len(head) < end or head[signature.offset : end] != signature.magic:
            continue
        if signature.trailer is not None:
            t_off, t_magic = signature.trailer
            if head[t_off : t_off + len(t_magic)] != t_magic:
                continue
        return signature.mime

    index = head.find(b"%PDF-")
    if 0 < index <= 1024:
        return "application/pdf"
    return None


def _sniff_zip_subtype(handle: BinaryIO) -> str:
    import zipfile

    position = handle.tell()
    try:
        handle.seek(0)
        with zipfile.ZipFile(handle) as archive:
            names = archive.namelist()
        blob = "\n".join(names).encode("utf-8", "ignore")
        for marker, mime in _ZIP_SUBTYPES:
            if marker in blob:
                return mime
        return "application/zip"
    except Exception:
        return "application/zip"
    finally:
        handle.seek(position)


def _mimes_agree(declared: Optional[str], detected: str) -> bool:
    if not declared:
        return True
    declared = declared.split(";")[0].strip().lower()
    if declared == detected:
        return True
    if declared == "application/octet-stream":
        return True
    return declared in _MIME_ALIASES.get(detected, frozenset())


def spool_stream(
    chunks: Iterable[bytes], *, max_bytes: int
) -> tuple[BinaryIO, int]:
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_MEMORY_BYTES)
    total = 0
    try:
        for chunk in chunks:
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise FileValidationError(
                    RejectionReason.TOO_LARGE,
                    f"Upload exceeds the {max_bytes}-byte limit.",
                    detail={"limit_bytes": max_bytes, "read_bytes": total},
                )
            spool.write(chunk)
    except Exception:
        spool.close()
        raise

    if total == 0:
        spool.close()
        raise FileValidationError(RejectionReason.EMPTY, "Uploaded file is empty.")

    spool.seek(0)
    return spool, total


async def spool_upload_file(upload: Any, *, max_bytes: int) -> tuple[BinaryIO, int]:
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_MEMORY_BYTES)
    total = 0
    try:
        while True:
            chunk = await upload.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise FileValidationError(
                    RejectionReason.TOO_LARGE,
                    f"Upload exceeds the {max_bytes}-byte limit.",
                    detail={"limit_bytes": max_bytes, "read_bytes": total},
                )
            spool.write(chunk)
    except Exception:
        spool.close()
        raise

    if total == 0:
        spool.close()
        raise FileValidationError(RejectionReason.EMPTY, "Uploaded file is empty.")

    spool.seek(0)
    return spool, total


def _probe_pdf(handle: BinaryIO, *, max_pages: int) -> tuple[int, list[str]]:
    from pypdf import PdfReader

    notes: list[str] = []
    handle.seek(0)
    try:
        reader = PdfReader(handle)
    except Exception as exc:
        raise FileValidationError(
            RejectionReason.CORRUPT, f"PDF could not be parsed: {exc}"
        ) from exc

    if getattr(reader, "is_encrypted", False):
        raise FileValidationError(
            RejectionReason.ENCRYPTED,
            "Password-protected PDFs are not supported.",
        )

    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise FileValidationError(
            RejectionReason.CORRUPT, f"PDF page tree is unreadable: {exc}"
        ) from exc

    if page_count <= 0:
        raise FileValidationError(RejectionReason.CORRUPT, "PDF has no pages.")
    if page_count > max_pages:
        raise FileValidationError(
            RejectionReason.TOO_MANY_PAGES,
            f"PDF has {page_count} pages, over the {max_pages}-page limit.",
            detail={"page_count": page_count, "limit": max_pages},
        )

    try:
        root = reader.trailer.get("/Root", {})
        for marker in ("/JavaScript", "/JS", "/OpenAction", "/AA", "/Launch"):
            if marker in root:
                notes.append(f"active_content:{marker}")
    except Exception:
        pass

    handle.seek(0)
    return page_count, notes


def _scrub_pdf(handle: BinaryIO) -> tuple[BinaryIO, int]:
    from pypdf import PdfReader, PdfWriter

    handle.seek(0)
    reader = PdfReader(handle)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata({})

    out = tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_MEMORY_BYTES)
    writer.write(out)
    size = out.tell()
    out.seek(0)
    return out, size


def _probe_and_scrub_image(handle: BinaryIO, mime: str) -> tuple[BinaryIO, int, str]:
    from PIL import Image, UnidentifiedImageError

    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

    try:
        handle.seek(0)
        try:
            with Image.open(handle) as probe:
                probe.verify()
        except Image.DecompressionBombError as exc:
            raise FileValidationError(
                RejectionReason.DECOMPRESSION_BOMB,
                f"Image declares more than {MAX_IMAGE_PIXELS} pixels: {exc}",
            ) from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise FileValidationError(
                RejectionReason.CORRUPT, f"Image could not be decoded: {exc}"
            ) from exc

        handle.seek(0)
        out = tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_MEMORY_BYTES)
        try:
            with Image.open(handle) as image:
                image_format = (image.format or "PNG").upper()
                save_format = "JPEG" if image_format in {"JPEG", "MPO"} else "PNG"
                clean = Image.frombytes(image.mode, image.size, image.tobytes())

                if save_format == "JPEG" and clean.mode not in {"RGB", "L"}:
                    clean = clean.convert("RGB")
                elif save_format == "PNG" and clean.mode == "P":
                    clean = clean.convert("RGBA")
                clean.save(out, format=save_format)
        except Image.DecompressionBombError as exc:
            out.close()
            raise FileValidationError(
                RejectionReason.DECOMPRESSION_BOMB,
                f"Image expanded past the pixel ceiling on decode: {exc}",
            ) from exc
        except (OSError, ValueError) as exc:
            out.close()
            raise FileValidationError(
                RejectionReason.CORRUPT, f"Image could not be re-encoded: {exc}"
            ) from exc

        size = out.tell()
        out.seek(0)
        return out, size, "image/jpeg" if save_format == "JPEG" else "image/png"
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def _hash_stream(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    handle.seek(0)
    while True:
        chunk = handle.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
    handle.seek(0)
    return digest.hexdigest()


def validate_spooled(
    handle: BinaryIO,
    size: int,
    *,
    declared_mime: Optional[str],
    original_filename: str,
    allowed_mimes: Iterable[str],
    max_pages: int,
    scrub_metadata: bool = True,
) -> ValidatedUpload:
    allowed = {m.split(";")[0].strip().lower() for m in allowed_mimes}
    notes: list[str] = []
    owned = handle

    try:
        handle.seek(0)
        head = handle.read(SNIFF_BYTES)
        handle.seek(0)

        detected = sniff_mime(head)
        if detected is None:
            raise FileValidationError(
                RejectionReason.UNKNOWN_SIGNATURE,
                "File type could not be determined from its contents.",
                declared_mime=declared_mime,
            )
        if detected == "application/zip":
            detected = _sniff_zip_subtype(handle)

        if not _mimes_agree(declared_mime, detected):
            raise FileValidationError(
                RejectionReason.MIME_MISMATCH,
                f"Declared type {declared_mime!r} does not match the file's "
                f"contents ({detected!r}).",
                declared_mime=declared_mime,
                detected_mime=detected,
            )

        if detected not in allowed:
            raise FileValidationError(
                RejectionReason.MIME_NOT_ALLOWED,
                f"{detected!r} is not an accepted document type.",
                declared_mime=declared_mime,
                detected_mime=detected,
                detail={"allowed": sorted(allowed)},
            )

        page_count: Optional[int] = None
        final_mime = detected
        scrubbed = False

        if detected == "application/pdf":
            page_count, pdf_notes = _probe_pdf(handle, max_pages=max_pages)
            notes.extend(pdf_notes)
            if scrub_metadata:
                scrubbed_handle, size = _scrub_pdf(handle)
                owned.close()
                owned = scrubbed_handle
                scrubbed = True

        elif detected in _IMAGE_MIMES:
            page_count = 1
            if scrub_metadata:
                scrubbed_handle, size, final_mime = _probe_and_scrub_image(
                    handle, detected
                )
                owned.close()
                owned = scrubbed_handle
                scrubbed = True
            else:
                from PIL import Image, UnidentifiedImageError

                previous_limit = Image.MAX_IMAGE_PIXELS
                Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
                handle.seek(0)
                try:
                    with Image.open(handle) as probe:
                        probe.verify()
                except Image.DecompressionBombError as exc:
                    raise FileValidationError(
                        RejectionReason.DECOMPRESSION_BOMB,
                        f"Image declares more than {MAX_IMAGE_PIXELS} pixels: {exc}",
                    ) from exc
                except (UnidentifiedImageError, OSError, ValueError) as exc:
                    raise FileValidationError(
                        RejectionReason.CORRUPT,
                        f"Image could not be decoded: {exc}",
                    ) from exc
                finally:
                    Image.MAX_IMAGE_PIXELS = previous_limit
                handle.seek(0)

        checksum = _hash_stream(owned)

        return ValidatedUpload(
            handle=owned,
            size=size,
            checksum_sha256=checksum,
            mime_type=final_mime,
            original_filename=original_filename,
            page_count=page_count,
            scrubbed=scrubbed,
            notes=notes,
        )

    except Exception:
        try:
            owned.close()
        except Exception:
            pass
        raise
